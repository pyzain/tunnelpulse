"""
TunnelPulse — Self-Healing WireGuard VPN Engine (Core Logic Layer)
====================================================================
Prepared by Zain Raza

This module contains ALL business logic for TunnelPulse. No GUI code
lives here (see tunnel_pulse_gui.py) — this file must be importable
and fully testable headlessly.

Design notes:
  * The tunnel data-plane (encryption/handshake) is delegated entirely
    to the official WireGuard-for-Windows binaries (wireguard.exe +
    the wintun.dll NDIS driver). This module never implements its own
    crypto — it orchestrates a well-audited implementation instead.
  * Everything here is thread/async-safe by design: the engine runs
    its supervisor loop on a background thread with its own asyncio
    event loop, and publishes state via a thread-safe EventBus that
    the GUI polls on its own timer. The GUI must never block on this
    module, and this module must never touch GUI widgets.
  * Every public entry point validates before acting and verifies
    after acting.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Optional

import psutil
import requests
from pydantic import BaseModel, Field, field_validator

# ---- CONSTANTS & PATHS ----

APP_NAME = "TunnelPulse"
APP_VERSION = "1.0.0"   # single source of truth: the build script and installer read it from here
APP_AUTHOR = "Zain Raza"

IS_WINDOWS = platform.system() == "Windows"


def _local_appdata() -> Path:
    """Resolve %LOCALAPPDATA%, falling back to the usual location if the
    variable isn't set."""
    raw = os.environ.get("LOCALAPPDATA")
    base = Path(raw) if raw else Path.home() / "AppData" / "Local"
    return base / APP_NAME


APP_DATA_DIR = _local_appdata()

_CREATE_NO_WINDOW = 0x08000000


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run an external command without flashing a console window. The
    compiled app is windowed, so every bare subprocess call would otherwise
    pop up a black cmd window (several times a minute while connected)."""
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout,
        creationflags=_CREATE_NO_WINDOW if IS_WINDOWS else 0,
    )
CONFIG_DIR = APP_DATA_DIR / "profiles"
LOG_DIR = APP_DATA_DIR / "logs"


def resource_dir() -> Path:
    """Locate bundled resources whether running from source or a
    PyInstaller --onefile executable (sys._MEIPASS)."""
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def ensure_dirs() -> None:
    """Create the data folders if they don't exist. Called before every
    write, so deleting them while the app runs doesn't break anything."""
    for d in (APP_DATA_DIR, CONFIG_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---- LOGGING ----

def configure_logging(debug: bool = False) -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_DIR / "tunnelpulse.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if debug:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        logger.addHandler(stream_handler)

    return logger


log = configure_logging()


# ---- EXCEPTIONS ----

class TunnelPulseError(Exception):
    """Base for all engine errors. Always carries a user-safe message."""

    def __init__(self, user_message: str, technical_detail: str = ""):
        self.user_message = user_message
        self.technical_detail = technical_detail
        super().__init__(f"{user_message} :: {technical_detail}")


class BinaryMissingError(TunnelPulseError):
    pass


class PrivilegeError(TunnelPulseError):
    pass


class ProfileError(TunnelPulseError):
    pass


class TunnelStartError(TunnelPulseError):
    pass


# ---- ENUMS & EVENTS ----

class ConnectionState(Enum):
    DISCONNECTED = auto()
    VALIDATING = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    DISCONNECTING = auto()
    DEGRADED = auto()      # up, but leak/handshake concerns detected
    FAILED = auto()


class EventType(Enum):
    STATE_CHANGED = auto()
    METRICS_TICK = auto()
    LOG_LINE = auto()
    ISSUE_DETECTED = auto()
    ISSUE_RESOLVED = auto()


@dataclass
class TunnelEvent:
    type: EventType
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """Thread-safe pub/sub. Engine runs on a background thread; GUI polls
    this queue on its own mainloop timer instead of receiving direct
    callbacks — avoids cross-thread widget access entirely."""

    def __init__(self) -> None:
        self._queue: Queue[TunnelEvent] = Queue()

    def publish(self, event: TunnelEvent) -> None:
        self._queue.put(event)

    def drain(self, max_items: int = 50) -> list[TunnelEvent]:
        events = []
        for _ in range(max_items):
            try:
                events.append(self._queue.get_nowait())
            except Empty:
                break
        return events


# ---- CONFIG MODELS (validated with pydantic when loaded or imported) ----

_WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$")


def _looks_like_wg_key(v: str) -> str:
    if not _WG_KEY_RE.match(v.strip()):
        raise ValueError("Not a valid WireGuard base64 key (expected 44 chars, padded).")
    return v.strip()


class PeerConfig(BaseModel):
    public_key: str
    endpoint_host: str
    endpoint_port: int = Field(ge=1, le=65535)
    allowed_ips: list[str] = Field(default_factory=lambda: ["0.0.0.0/0", "::/0"])
    persistent_keepalive: int = Field(default=25, ge=0, le=120)
    preshared_key: Optional[str] = None

    @field_validator("public_key")
    @classmethod
    def _v_pub(cls, v: str) -> str:
        return _looks_like_wg_key(v)


class InterfaceConfig(BaseModel):
    private_key: str
    address: list[str] = Field(min_length=1)   # e.g. ["10.66.0.2/32"]
    dns: list[str] = Field(default_factory=lambda: ["1.1.1.1", "9.9.9.9"])
    mtu: int = Field(default=1420, ge=576, le=1500)

    @field_validator("private_key")
    @classmethod
    def _v_priv(cls, v: str) -> str:
        return _looks_like_wg_key(v)


class TunnelProfile(BaseModel):
    """One saved VPN profile — one server endpoint. Fully self-contained;
    the file on disk IS this model, round-tripped through JSON."""

    profile_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    interface: InterfaceConfig
    peer: PeerConfig
    kill_switch: bool = True
    created_at: float = Field(default_factory=time.time)

    def to_wg_conf_text(self) -> str:
        """Render as a standard [Interface]/[Peer] .conf file, the format
        wireguard.exe expects natively."""
        i, p = self.interface, self.peer
        lines = [
            "[Interface]",
            f"PrivateKey = {i.private_key}",
            f"Address = {', '.join(i.address)}",
        ]
        if i.dns:
            lines.append(f"DNS = {', '.join(i.dns)}")
        lines += [
            f"MTU = {i.mtu}",
            "",
            "[Peer]",
            f"PublicKey = {p.public_key}",
        ]
        if p.preshared_key:
            lines.append(f"PresharedKey = {p.preshared_key}")
        lines += [
            f"AllowedIPs = {', '.join(p.allowed_ips)}",
            f"Endpoint = {p.endpoint_host}:{p.endpoint_port}",
            f"PersistentKeepalive = {p.persistent_keepalive}",
        ]
        return "\n".join(lines) + "\n"


class ConfigStore:
    """Saves and loads profiles as JSON. A corrupt profile file is moved
    to a _corrupt/ subfolder instead of stopping the app from starting."""

    def __init__(self, directory: Path = CONFIG_DIR):
        self.directory = directory
        ensure_dirs()
        self.directory.mkdir(parents=True, exist_ok=True)  # covers non-default dirs too

    def list_profiles(self) -> list[TunnelProfile]:
        profiles: list[TunnelProfile] = []
        for f in sorted(self.directory.glob("*.json")):
            try:
                profiles.append(TunnelProfile.model_validate_json(f.read_text("utf-8")))
            except Exception as exc:  # noqa: BLE001 - any bad file should be skipped
                log.warning("Quarantining corrupt profile %s: %s", f.name, exc)
                corrupt_dir = self.directory / "_corrupt"
                corrupt_dir.mkdir(exist_ok=True)
                try:
                    shutil.move(str(f), str(corrupt_dir / f.name))
                except OSError:
                    pass
        return profiles

    def save(self, profile: TunnelProfile) -> Path:
        path = self.directory / f"{profile.profile_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic on Windows NTFS — avoids half-written configs
        return path

    def delete(self, profile_id: str) -> None:
        path = self.directory / f"{profile_id}.json"
        path.unlink(missing_ok=True)

    def import_wg_conf(self, conf_text: str, name: str) -> TunnelProfile:
        """Parse a standard WireGuard .conf (e.g. exported by a VPS you
        control, or given to you by a WireGuard-compatible provider)."""
        section = None
        kv: dict[str, dict[str, str]] = {"Interface": {}, "Peer": {}}
        conf_text = conf_text.lstrip("\ufeff")  # byte-order mark from Notepad
        for raw_line in conf_text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section not in kv or "=" not in line:
                continue
            key, _, val = line.partition("=")
            kv[section][key.strip()] = val.strip()

        try:
            iface = InterfaceConfig(
                private_key=kv["Interface"]["PrivateKey"],
                address=[a.strip() for a in kv["Interface"].get("Address", "").split(",") if a.strip()],
                dns=[d.strip() for d in kv["Interface"].get("DNS", "1.1.1.1").split(",") if d.strip()],
                mtu=int(kv["Interface"].get("MTU", 1420)),
            )
            host, _, port = kv["Peer"]["Endpoint"].rpartition(":")
            peer = PeerConfig(
                public_key=kv["Peer"]["PublicKey"],
                endpoint_host=host,
                endpoint_port=int(port),
                allowed_ips=[a.strip() for a in kv["Peer"].get("AllowedIPs", "0.0.0.0/0").split(",")],
                persistent_keepalive=int(kv["Peer"].get("PersistentKeepalive", 25)),
                preshared_key=kv["Peer"].get("PresharedKey") or None,
            )
        except (KeyError, ValueError) as exc:
            raise ProfileError(
                "That .conf file is missing required fields.",
                technical_detail=str(exc),
            ) from exc

        profile = TunnelProfile(name=name, interface=iface, peer=peer)
        self.save(profile)
        return profile


# ---- PRIVILEGE HANDLING ----

class PrivilegeManager:
    @staticmethod
    def is_admin() -> bool:
        if not IS_WINDOWS:
            return os.geteuid() == 0  # dev-machine convenience only
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def relaunch_as_admin(extra_args: Optional[list[str]] = None) -> bool:
        """Re-launch the app elevated via UAC. Only called right before an
        action that needs it (installing the tunnel service), never at
        startup. extra_args are appended, e.g. ["--connect", profile_id]."""
        if not IS_WINDOWS:
            raise PrivilegeError("Elevation is only meaningful on Windows.")
        import sys
        try:
            own_args = [a for a in sys.argv[1:]]
            if "--connect" in own_args:  # don't stack a previous --connect request
                i = own_args.index("--connect")
                del own_args[i:i + 2]
            own_args += extra_args or []
            args = own_args if getattr(sys, "frozen", False) else [os.path.abspath(sys.argv[0]), *own_args]
            params = " ".join(f'"{a}"' for a in args)
            ret = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", sys.executable, params, None, 1
            )
            return ret > 32
        except Exception as exc:  # noqa: BLE001
            raise PrivilegeError("Could not request administrator rights.", str(exc)) from exc


# ---- WIREGUARD BINARY LOCATOR ----

class WireGuardBinaryLocator:
    """Finds wireguard.exe (tunnel service manager) and the optional
    wg.exe (status/debug CLI shipped in newer WireGuard-for-Windows
    releases). Searches, in order: bundled resources -> PATH -> the
    official install location -> a short list of common alternates.
    """

    CANDIDATE_DIRS = [
        Path(r"C:\Program Files\WireGuard"),
        Path(r"C:\Program Files (x86)\WireGuard"),
    ]

    def __init__(self) -> None:
        self.wireguard_exe: Optional[Path] = None
        self.wg_exe: Optional[Path] = None
        self._locate()

    def _locate(self) -> None:
        search_dirs = [resource_dir() / "bin", *self.CANDIDATE_DIRS]
        path_hit = shutil.which("wireguard.exe") or shutil.which("wireguard")
        if path_hit:
            search_dirs.insert(0, Path(path_hit).parent)

        for d in search_dirs:
            wg_gui = d / "wireguard.exe"
            wg_cli = d / "wg.exe"
            if self.wireguard_exe is None and wg_gui.exists():
                self.wireguard_exe = wg_gui
            if self.wg_exe is None and wg_cli.exists():
                self.wg_exe = wg_cli

    def require_wireguard(self) -> Path:
        if self.wireguard_exe is None:
            raise BinaryMissingError(
                "WireGuard's Windows component isn't installed.",
                "wireguard.exe not found in bundled bin/, PATH, or Program Files. "
                "Install the official WireGuard for Windows package once, or drop "
                "wireguard.exe + wintun.dll into the app's bin/ folder.",
            )
        return self.wireguard_exe


# ---- TUNNEL CONTROLLER (wraps the official wireguard.exe) ----

class TunnelController:
    SERVICE_PREFIX = "TunnelPulse"

    def __init__(self, locator: WireGuardBinaryLocator):
        self.locator = locator

    def _service_name(self, profile: TunnelProfile) -> str:
        return f"{self.SERVICE_PREFIX}-{profile.profile_id}"

    def _conf_path(self, profile: TunnelProfile) -> Path:
        ensure_dirs()
        # WireGuard requires the tunnel name to match the .conf filename.
        p = APP_DATA_DIR / f"{self._service_name(profile)}.conf"
        p.write_text(profile.to_wg_conf_text(), encoding="utf-8", newline="\n")
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return p

    def start(self, profile: TunnelProfile) -> None:
        if not PrivilegeManager.is_admin():
            raise PrivilegeError(
                "Starting a tunnel requires administrator rights (installs a "
                "network adapter service). Elevate and try again."
            )
        exe = self.locator.require_wireguard()
        # A service left behind by a crash would make the install fail.
        _run([str(exe), "/uninstalltunnelservice", self._service_name(profile)], timeout=25)
        conf_path = self._conf_path(profile)
        result = _run([str(exe), "/installtunnelservice", str(conf_path)], timeout=25)
        if result.returncode != 0:
            raise TunnelStartError(
                "The tunnel service failed to start.",
                f"exit={result.returncode} stderr={result.stderr.strip()}",
            )
        if not self._wait_for_adapter(self._service_name(profile), timeout=10):
            raise TunnelStartError(
                "WireGuard reported success but no network adapter appeared.",
                "adapter not found in psutil.net_if_addrs() within timeout",
            )
        # WireGuard creates tunnel services that start automatically at boot.
        # TunnelPulse only monitors a tunnel while it's open, so make it start
        # on demand instead: otherwise, after a restart, a full-tunnel profile
        # with an unreachable server would block all traffic with nothing
        # telling the user why.
        sc = _run(["sc.exe", "config", f"WireGuardTunnel${self._service_name(profile)}", "start=", "demand"], timeout=15)
        if sc.returncode != 0:
            log.warning("Couldn't set the tunnel service to manual start: %s", sc.stdout.strip() or sc.stderr.strip())

    def stop(self, profile: TunnelProfile) -> None:
        exe = self.locator.require_wireguard()
        _run([str(exe), "/uninstalltunnelservice", self._service_name(profile)], timeout=25)
        conf = self._conf_path_only(profile)
        conf.unlink(missing_ok=True)

    def _conf_path_only(self, profile: TunnelProfile) -> Path:
        return APP_DATA_DIR / f"{self._service_name(profile)}.conf"

    @staticmethod
    def _wait_for_adapter(service_name: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if service_name in psutil.net_if_addrs():
                return True
            time.sleep(0.5)
        return False

    def adapter_name(self, profile: TunnelProfile) -> str:
        return self._service_name(profile)

    def running_tunnel_ids(self) -> list[str]:
        """Profile ids of TunnelPulse tunnels that are up right now, e.g. one
        left running after the app crashed. Doesn't need admin rights."""
        prefix = f"{self.SERVICE_PREFIX}-"
        return [name[len(prefix):] for name in psutil.net_if_addrs() if name.startswith(prefix)]

    def is_adapter_up(self, profile: TunnelProfile) -> bool:
        stats = psutil.net_if_stats().get(self.adapter_name(profile))
        return bool(stats and stats.isup)

    def last_handshake_age(self, profile: TunnelProfile) -> Optional[float]:
        """Returns seconds since last WireGuard handshake, if wg.exe is
        available for status introspection. None if unknown (degrades
        gracefully rather than failing the whole health check)."""
        if self.locator.wg_exe is None:
            return None
        try:
            result = _run([str(self.locator.wg_exe), "show", self.adapter_name(profile), "latest-handshakes"], timeout=5)
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                handshake_epoch = int(parts[1])
                if handshake_epoch == 0:
                    return None
                return max(0.0, time.time() - handshake_epoch)
        except Exception as exc:  # noqa: BLE001
            log.debug("handshake introspection unavailable: %s", exc)
        return None


# ---- KILL SWITCH ----

class KillSwitchManager:
    """WireGuard for Windows already enforces a kill switch through Windows
    Filtering Platform rules whenever a tunnel has a single peer whose
    AllowedIPs contains a /0 route (0.0.0.0/0 or ::/0). Those rules belong
    to the tunnel service itself: they appear when it starts and vanish when
    it is uninstalled, so a crash can never leave the PC without internet.

    This class checks that a profile qualifies for that behaviour rather than
    adding Windows Firewall rules of its own. A blanket outbound block rule
    cannot work here: in Windows Firewall, block rules override allow rules,
    so it would also block the tunnel's own traffic."""

    LEGACY_RULE_PREFIX = "TunnelPulse-KillSwitch"   # rules written by early development builds

    @staticmethod
    def qualifies(profile: TunnelProfile) -> bool:
        return any(ip.strip().endswith("/0") for ip in profile.peer.allowed_ips)

    def engage(self, profile: TunnelProfile) -> bool:
        active = self.qualifies(profile)
        if active:
            log.info("Kill switch active (WireGuard full-tunnel mode) for %s", profile.profile_id)
        else:
            log.warning("Kill switch unavailable for %s: no /0 route in AllowedIPs", profile.profile_id)
        return active

    def disengage(self, profile: TunnelProfile) -> None:
        # Nothing to undo for the current design; clear any rules an older
        # build may have left behind.
        rule = f"{self.LEGACY_RULE_PREFIX}-{profile.profile_id}"
        for suffix in ("", "-allow"):
            try:
                _run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}{suffix}"], timeout=10)
            except Exception as exc:  # noqa: BLE001
                log.debug("legacy rule cleanup skipped: %s", exc)


# ---- BANDWIDTH MONITOR ----

@dataclass
class BandwidthSample:
    down_mbps: float
    up_mbps: float
    total_down_mb: float
    total_up_mb: float


class BandwidthMonitor:
    def __init__(self, adapter_name: str, window: int = 30):
        self.adapter_name = adapter_name
        self.history: deque[BandwidthSample] = deque(maxlen=window)
        self._last_counters: Optional[psutil._common.snetio] = None  # type: ignore[attr-defined]
        self._last_time: float = 0.0

    def sample(self) -> Optional[BandwidthSample]:
        counters = psutil.net_io_counters(pernic=True).get(self.adapter_name)
        now = time.time()
        if counters is None:
            return None
        if self._last_counters is not None:
            dt = max(now - self._last_time, 0.001)
            down_mbps = ((counters.bytes_recv - self._last_counters.bytes_recv) * 8 / 1_000_000) / dt
            up_mbps = ((counters.bytes_sent - self._last_counters.bytes_sent) * 8 / 1_000_000) / dt
            sample = BandwidthSample(
                down_mbps=round(max(down_mbps, 0.0), 2),
                up_mbps=round(max(up_mbps, 0.0), 2),
                total_down_mb=round(counters.bytes_recv / 1_000_000, 1),
                total_up_mb=round(counters.bytes_sent / 1_000_000, 1),
            )
            self.history.append(sample)
        else:
            sample = None
        self._last_counters = counters
        self._last_time = now
        return sample


# ---- DNS LEAK GUARD ----

class DNSLeakGuard:
    """Confirms the DNS servers actually in use match the tunnel's
    configured DNS, rather than a leaked local/ISP resolver."""

    @staticmethod
    def system_dns_servers() -> list[str]:
        if not IS_WINDOWS:
            return []
        try:
            result = _run(["ipconfig", "/all"], timeout=5)
            return re.findall(r"DNS Servers[.\s]*:\s*([\d.]+)", result.stdout)
        except Exception as exc:  # noqa: BLE001
            log.debug("DNS introspection failed: %s", exc)
            return []

    def check(self, expected_dns: list[str]) -> bool:
        """Returns True if at least one active DNS server matches the
        tunnel profile's expected DNS (best-effort — ipconfig output
        varies across Windows builds, so this fails open to 'unknown'
        rather than false-alarming the user)."""
        if not expected_dns:
            return True  # profile sets no DNS servers, so there is nothing to compare
        active = self.system_dns_servers()
        if not active:
            return True  # can't tell, so don't report a leak
        return any(d in expected_dns for d in active)


# ---- ADAPTIVE LINK OPTIMIZER (MTU probing) ----

class AdaptiveLinkOptimizer:
    """Finds the largest tunnel MTU the current network can carry without
    fragmenting. Too large an MTU causes fragmented or dropped packets; too
    small wastes bandwidth on headers. The result is saved to the profile
    and used on the next connect."""

    IP_ICMP_HEADERS = 28      # IPv4 (20) + ICMP (8) around a ping payload
    WIREGUARD_OVERHEAD = 80   # worst case (IPv6 outer header); 1500 - 80 = 1420 default
    MIN_MTU, MAX_MTU = 1280, 1420

    def __init__(self, endpoint_host: str):
        self.endpoint_host = endpoint_host

    def probe_optimal_mtu(self, low: int = 1200, high: int = 1472) -> Optional[int]:
        """Binary-search the largest unfragmented ping payload to the server,
        then convert it to a tunnel MTU. Returns None if the server never
        answered (it may block ping), rather than guessing."""
        if not IS_WINDOWS:
            return None
        best: Optional[int] = None
        while low <= high:
            mid = (low + high) // 2
            if self._df_ping_succeeds(mid):
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        if best is None:
            return None
        path_mtu = best + self.IP_ICMP_HEADERS
        return max(self.MIN_MTU, min(self.MAX_MTU, path_mtu - self.WIREGUARD_OVERHEAD))

    def _df_ping_succeeds(self, payload_size: int) -> bool:
        try:
            result = _run(["ping", "-f", "-l", str(payload_size), "-n", "1", "-w", "1500",
                           self.endpoint_host], timeout=5)
        except Exception:  # noqa: BLE001
            return False
        # Windows ping exits 0 even for "Destination host unreachable", so
        # only a reply carrying a TTL counts as success.
        return "TTL=" in result.stdout.upper() and "FRAGMENTED" not in result.stdout.upper()


# ---- PUBLIC IP PROBE ----

@dataclass
class ProtectionStatus:
    baseline_ip: Optional[str]     # public IP before connecting
    current_ip: Optional[str]      # public IP right now
    protected: bool                # current_ip differs from baseline and is known
    checked_at: float = field(default_factory=time.time)

    @property
    def label(self) -> str:
        if self.current_ip is None:
            return "Unable to verify"
        if self.baseline_ip is None:
            return "No baseline captured"
        return "Protected" if self.protected else "Not protected — same IP as before"


class PublicIPProbe:
    """Fetches the machine's public IP. The adapter can be up and the
    config correct while traffic still goes out the normal connection, so
    comparing the public IP before and after connecting is the check that
    shows traffic really goes through the tunnel.

    Tries several providers in order and returns None if none respond.
    """

    PROVIDERS = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]

    def fetch(self, timeout: float = 4.0) -> Optional[str]:
        for url in self.PROVIDERS:
            try:
                resp = requests.get(url, timeout=timeout)
                resp.raise_for_status()
                text = resp.text.strip()
                if url.endswith("format=json"):
                    return resp.json().get("ip")
                # bare-text providers occasionally wrap in quotes/whitespace
                candidate = text.strip('"')
                socket.inet_aton(candidate)  # cheap IPv4 sanity check; raises if malformed
                return candidate
            except Exception as exc:  # noqa: BLE001 - try next provider
                log.debug("IP probe provider %s failed: %s", url, exc)
                continue
        return None


# ---- PRE/POST-CONNECT CHECKS ----

@dataclass
class Issue:
    severity: str  # "info" | "warning" | "critical"
    message: str
    auto_fixed: bool = False


class SelfHealingDoctor:
    def __init__(self, locator: WireGuardBinaryLocator):
        self.locator = locator

    def preflight(self, profile: TunnelProfile) -> list[Issue]:
        issues: list[Issue] = []

        ensure_dirs()  # recreate data folders if missing

        if not IS_WINDOWS:
            issues.append(Issue("critical", "TunnelPulse's tunnel layer targets Windows only."))

        if self.locator.wireguard_exe is None:
            issues.append(Issue(
                "critical",
                "wireguard.exe not found. Install WireGuard for Windows, or place "
                "wireguard.exe + wintun.dll in the app's bin/ folder.",
            ))

        if not PrivilegeManager.is_admin():
            issues.append(Issue(
                "warning",
                "Not running as administrator — connecting will prompt for elevation.",
            ))

        try:
            socket.gethostbyname(profile.peer.endpoint_host)
        except socket.gaierror:
            issues.append(Issue(
                "critical",
                f"Can't resolve endpoint host '{profile.peer.endpoint_host}'. "
                "Check the server address and your current network connection.",
            ))

        for existing in psutil.net_if_addrs():
            if existing == f"{TunnelController.SERVICE_PREFIX}-{profile.profile_id}":
                issues.append(Issue(
                    "info",
                    "A previous session's adapter is still present — will be replaced.",
                ))

        return issues

    def postflight(self, controller: TunnelController, profile: TunnelProfile) -> list[Issue]:
        issues: list[Issue] = []
        if not controller.is_adapter_up(profile):
            issues.append(Issue("critical", "Adapter did not come up after connect."))
        handshake_age = controller.last_handshake_age(profile)
        if handshake_age is not None and handshake_age > 180:
            issues.append(Issue("warning", "No recent WireGuard handshake — server may be unreachable."))
        return issues


# ---- ENGINE ----

class TunnelPulseEngine:
    """Public API surface the GUI talks to. Owns a background thread
    running an asyncio supervisor loop that handles health checks,
    reconnects, and metrics — the GUI thread never blocks on any of it."""

    RECONNECT_BASE_DELAY = 2.0
    RECONNECT_MAX_DELAY = 60.0
    HEALTH_CHECK_INTERVAL = 5.0
    MAX_RECONNECT_ATTEMPTS = 8
    NO_HANDSHAKE_GRACE = 30.0   # seconds after connecting before "server isn't answering" is reported

    def __init__(self):
        ensure_dirs()
        self.bus = EventBus()
        self.config_store = ConfigStore()
        self.locator = WireGuardBinaryLocator()
        self.controller = TunnelController(self.locator)
        self.kill_switch = KillSwitchManager()
        self.doctor = SelfHealingDoctor(self.locator)
        self.ip_probe = PublicIPProbe()

        self.state = ConnectionState.DISCONNECTED
        self.active_profile: Optional[TunnelProfile] = None
        self._bandwidth: Optional[BandwidthMonitor] = None
        self._dns_guard = DNSLeakGuard()
        self._baseline_ip: Optional[str] = None
        self.last_protection_status: Optional[ProtectionStatus] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._reconnect_attempts = 0
        self._connected_at = 0.0
        self._degraded_reasons: set[str] = set()

    # ---- lifecycle ----

    def start_background_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_loop, name="TunnelPulse-Supervisor", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop_flag.set()
        if self.active_profile:
            self.disconnect()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._supervisor())
        except RuntimeError:
            pass  # shutdown() stops the loop mid-sleep; that is the normal exit path
        finally:
            self._loop.close()

    async def _supervisor(self) -> None:
        while not self._stop_flag.is_set():
            profile = self.active_profile
            if profile:
                if self.state == ConnectionState.RECONNECTING:
                    self._self_heal_reconnect(profile)
                elif self.state in (ConnectionState.CONNECTED, ConnectionState.DEGRADED):
                    self._tick_health()
                    if self.state in (ConnectionState.CONNECTED, ConnectionState.DEGRADED):
                        self._tick_bandwidth()
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

    # ---- public API ----

    def connect(self, profile: TunnelProfile) -> None:
        self._set_state(ConnectionState.VALIDATING)
        self._baseline_ip = self.ip_probe.fetch()  # capture BEFORE the tunnel exists
        issues = self.doctor.preflight(profile)
        for issue in issues:
            self.bus.publish(TunnelEvent(EventType.ISSUE_DETECTED, {"issue": issue}))
        if any(i.severity == "critical" for i in issues):
            self._set_state(ConnectionState.FAILED)
            raise TunnelStartError("Pre-flight checks failed — see issues panel.")

        self._set_state(ConnectionState.CONNECTING)
        try:
            self.controller.start(profile)
        except TunnelPulseError:
            self._set_state(ConnectionState.FAILED)
            raise

        post_issues = self.doctor.postflight(self.controller, profile)
        if any(i.severity == "critical" for i in post_issues):
            self._set_state(ConnectionState.FAILED)
            self.controller.stop(profile)
            raise TunnelStartError("Tunnel came up but failed verification.")

        if profile.kill_switch and not self.kill_switch.engage(profile):
            self.bus.publish(TunnelEvent(EventType.ISSUE_DETECTED, {"issue": Issue(
                "warning",
                "Kill switch is off: this profile only routes some traffic (no 0.0.0.0/0 in AllowedIPs).",
            )}))

        self.active_profile = profile
        self._bandwidth = BandwidthMonitor(self.controller.adapter_name(profile))
        self._reconnect_attempts = 0
        self._connected_at = time.time()
        self._degraded_reasons = set()
        self.start_background_loop()
        self._set_state(ConnectionState.CONNECTED)
        threading.Thread(target=self.verify_protection, daemon=True).start()  # non-blocking

    def disconnect(self) -> None:
        if not self.active_profile:
            return
        self._set_state(ConnectionState.DISCONNECTING)
        profile = self.active_profile
        try:
            if profile.kill_switch:
                self.kill_switch.disengage(profile)
            self.controller.stop(profile)
        finally:
            self.active_profile = None
            self._bandwidth = None
            self._baseline_ip = None
            self.last_protection_status = None
            self._set_state(ConnectionState.DISCONNECTED)

    def get_metrics(self) -> Optional[BandwidthSample]:
        if not self._bandwidth:
            return None
        return self._bandwidth.history[-1] if self._bandwidth.history else None

    def verify_protection(self) -> ProtectionStatus:
        """Compare the public IP captured before connecting with the current
        one. Safe to call while disconnected (reports protected=False)."""
        current_ip = self.ip_probe.fetch()
        protected = bool(
            self.active_profile
            and current_ip
            and self._baseline_ip
            and current_ip != self._baseline_ip
        )
        status = ProtectionStatus(
            baseline_ip=self._baseline_ip,
            current_ip=current_ip,
            protected=protected,
        )
        self.last_protection_status = status
        if self.active_profile and not protected:
            self.bus.publish(TunnelEvent(
                EventType.ISSUE_DETECTED,
                {"issue": Issue("critical", "Public IP unchanged — traffic may not be routing through the tunnel.")},
            ))
        return status

    def optimize_link(self) -> Optional[int]:
        """On-demand MTU probe against the active peer. Returns the
        suggested MTU; caller (GUI) decides whether to save + reconnect."""
        if not self.active_profile:
            return None
        optimizer = AdaptiveLinkOptimizer(self.active_profile.peer.endpoint_host)
        return optimizer.probe_optimal_mtu()

    def save_mtu(self, mtu: int) -> TunnelProfile:
        """Store a probed MTU in the active profile so the next connect uses it."""
        if not self.active_profile:
            raise ProfileError("Connect first, then optimize the link.")
        updated = self.active_profile.model_copy(deep=True)
        updated.interface.mtu = mtu
        self.config_store.save(updated)
        return updated

    # ---- internal ----

    def _set_state(self, new_state: ConnectionState) -> None:
        self.state = new_state
        log.info("State -> %s", new_state.name)
        self.bus.publish(TunnelEvent(EventType.STATE_CHANGED, {"state": new_state}))

    def _tick_bandwidth(self) -> None:
        if not self._bandwidth:
            return
        sample = self._bandwidth.sample()
        if sample:
            self.bus.publish(TunnelEvent(EventType.METRICS_TICK, {"sample": sample}))

    def _tick_health(self) -> None:
        profile = self.active_profile
        if not profile:
            return

        adapter_ok = self.controller.is_adapter_up(profile)
        handshake_age = self.controller.last_handshake_age(profile)

        unhealthy = (not adapter_ok) or (handshake_age is not None and handshake_age > 180)
        if unhealthy:
            log.warning("Health check failed (adapter_ok=%s handshake_age=%s) — self-healing reconnect.",
                        adapter_ok, handshake_age)
            self._self_heal_reconnect(profile)
            return

        # Problems that a reconnect can't fix: report them and mark the
        # session as degraded, but keep the tunnel up.
        problems: set[str] = set()
        if not self._dns_guard.check(profile.interface.dns):
            problems.add("Possible DNS leak detected — traffic may bypass the tunnel's DNS.")
        never_answered = (
            self.controller.locator.wg_exe is not None
            and handshake_age is None
            and time.time() - self._connected_at > self.NO_HANDSHAKE_GRACE
        )
        if never_answered:
            problems.add("The server hasn't answered yet. Check the server address, port and keys in the .conf file.")

        for text in sorted(problems - self._degraded_reasons):
            self.bus.publish(TunnelEvent(EventType.ISSUE_DETECTED, {"issue": Issue("warning", text)}))
        if problems:
            self._set_state(ConnectionState.DEGRADED)
        elif self.state == ConnectionState.DEGRADED:
            self._set_state(ConnectionState.CONNECTED)
            self.bus.publish(TunnelEvent(EventType.ISSUE_RESOLVED, {"issue": Issue("info", "Connection healthy again.")}))
        self._degraded_reasons = problems

    def _self_heal_reconnect(self, profile: TunnelProfile) -> None:
        self._set_state(ConnectionState.RECONNECTING)
        self._reconnect_attempts += 1
        delay = min(
            self.RECONNECT_BASE_DELAY * (2 ** (self._reconnect_attempts - 1)),
            self.RECONNECT_MAX_DELAY,
        )
        log.info("Reconnect attempt #%d in %.1fs", self._reconnect_attempts, delay)

        try:
            self.controller.stop(profile)
        except TunnelPulseError:
            pass
        time.sleep(delay)
        if self.active_profile is not profile:
            return  # user disconnected while we were waiting

        try:
            self.controller.start(profile)
            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_attempts = 0
            self._connected_at = time.time()
            self._degraded_reasons = set()
            self.bus.publish(TunnelEvent(EventType.ISSUE_RESOLVED, {"issue": Issue("info", "Tunnel restored.")}))
        except TunnelPulseError as exc:
            log.error("Reconnect failed: %s", exc.user_message)
            if self._reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
                try:
                    self.controller.stop(profile)
                except TunnelPulseError:
                    pass
                self.active_profile = None
                self._bandwidth = None
                self._reconnect_attempts = 0
                self.bus.publish(TunnelEvent(EventType.ISSUE_DETECTED, {"issue": Issue(
                    "critical",
                    f"Gave up after {self.MAX_RECONNECT_ATTEMPTS} reconnect attempts. Check the server, then connect again.",
                )}))
                self._set_state(ConnectionState.FAILED)
            else:
                self._set_state(ConnectionState.RECONNECTING)
