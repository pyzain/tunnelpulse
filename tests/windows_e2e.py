"""End-to-end test on a real Windows machine (run by GitHub Actions).

Needs administrator rights and WireGuard for Windows (the installer test
installs it just before this runs). A second WireGuard tunnel on the same
machine acts as the VPN server, listening on 127.0.0.1, so TunnelPulse's
engine gets a real encrypted handshake without any outside server. The client
only routes 10.99.0.1/32, so the machine's own internet connection is left
alone and there's no kill switch involved.

Checks, in order:
  1. connect: tunnel service installed, adapter up, handshake with the server
  2. the tunnel service is set to start on demand, not at boot
  3. self-healing: delete the tunnel behind the engine's back, it comes back
  4. disconnect removes the tunnel service
  5. a server that never answers is reported (state DEGRADED + warning)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tunnel_pulse_core as core  # noqa: E402

WG_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WireGuard"
WIREGUARD = WG_DIR / "wireguard.exe"
WG = WG_DIR / "wg.exe"
SERVER = "TPTestServer"
SERVER_PORT = 51820
SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY")
State = core.ConnectionState


def run(*args, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], input=stdin, capture_output=True, text=True, timeout=60)


def genkey() -> tuple[str, str]:
    private = run(WG, "genkey").stdout.strip()
    public = run(WG, "pubkey", stdin=private).stdout.strip()
    return private, public


def service_exists(tunnel: str) -> bool:
    return run("sc.exe", "query", f"WireGuardTunnel${tunnel}").returncode == 0


def wait_for(condition, timeout: float, step: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(step)
    return condition()


def report(line: str) -> None:
    print(line, flush=True)
    if SUMMARY:
        with open(SUMMARY, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def diagnostics() -> str:
    parts = ["wg show:", run(WG, "show").stdout.strip() or "(nothing)"]
    services = run("powershell", "-NoProfile", "-Command",
                   "Get-Service 'WireGuardTunnel*' | Format-Table -AutoSize Name,Status,StartType | Out-String")
    parts += ["services:", services.stdout.strip() or "(none)"]
    log_file = core.LOG_DIR / "tunnelpulse.log"
    if log_file.exists():
        parts += ["tunnelpulse.log (last 25 lines):", *log_file.read_text(encoding="utf-8").splitlines()[-25:]]
    return "\n".join(parts)


def annotate(title: str, text: str) -> None:
    # GitHub annotations are one line; %0A becomes a line break.
    print(f"::error title={title}::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"), flush=True)


def check(ok: bool, what: str) -> None:
    if ok:
        report(f"- [x] {what}")
        return
    details = diagnostics()
    report(f"- [ ] **{what}**")
    report("```\n" + details + "\n```")
    annotate("Real tunnel test", f"{what}\n{details}")
    raise SystemExit(1)


class StateRecorder:
    """Samples engine.state in the background, so short-lived states like
    RECONNECTING aren't missed."""

    def __init__(self, engine: core.TunnelPulseEngine):
        self.engine, self.seen, self._stop = engine, [], threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.seen or self.seen[-1] != self.engine.state:
                self.seen.append(self.engine.state)
            time.sleep(0.05)

    def stop(self) -> list[State]:
        self._stop.set()
        return self.seen


def main() -> int:
    report("### Real tunnel test")
    check(core.PrivilegeManager.is_admin(), "running as administrator")
    check(WIREGUARD.exists() and WG.exists(), f"WireGuard installed at {WG_DIR}")

    server_private, server_public = genkey()
    client_private, client_public = genkey()
    workdir = Path(tempfile.mkdtemp(prefix="tp-e2e-"))
    server_conf = workdir / f"{SERVER}.conf"
    server_conf.write_text(
        "[Interface]\n"
        f"PrivateKey = {server_private}\n"
        f"ListenPort = {SERVER_PORT}\n"
        "Address = 10.99.0.1/32\n\n"
        "[Peer]\n"
        f"PublicKey = {client_public}\n"
        "AllowedIPs = 10.99.0.2/32\n",
        encoding="utf-8",
    )
    run(WIREGUARD, "/installtunnelservice", server_conf)
    check(wait_for(lambda: service_exists(SERVER), 30), "local test server tunnel is running")

    engine = core.TunnelPulseEngine()
    engine.HEALTH_CHECK_INTERVAL = 2.0
    engine.RECONNECT_BASE_DELAY = 1.0
    engine.NO_HANDSHAKE_GRACE = 10.0
    try:
        profile = core.TunnelProfile(
            name="CI loopback",
            interface=core.InterfaceConfig(private_key=client_private, address=["10.99.0.2/32"], dns=[]),
            peer=core.PeerConfig(public_key=server_public, endpoint_host="127.0.0.1",
                                 endpoint_port=SERVER_PORT, allowed_ips=["10.99.0.1/32"],
                                 persistent_keepalive=5),
        )
        engine.config_store.save(profile)
        tunnel = engine.controller.adapter_name(profile)

        # 1. connect
        engine.connect(profile)
        check(engine.state == State.CONNECTED, f"connect() finished in state CONNECTED (got {engine.state.name})")
        check(service_exists(tunnel), f"tunnel service WireGuardTunnel${tunnel} exists")
        check(engine.controller.is_adapter_up(profile), f"network adapter '{tunnel}' is up")
        check(wait_for(lambda: engine.controller.last_handshake_age(profile) is not None, 30),
              "WireGuard handshake with the server")

        # 2. no auto-start at boot
        qc = run("sc.exe", "qc", f"WireGuardTunnel${tunnel}").stdout
        check("DEMAND_START" in qc, "tunnel service is set to start on demand (not at boot)")

        time.sleep(engine.HEALTH_CHECK_INTERVAL * 2.5)
        check(engine.state == State.CONNECTED, f"health checks keep it CONNECTED (got {engine.state.name})")

        # 3. self-healing
        recorder = StateRecorder(engine)
        run(WIREGUARD, "/uninstalltunnelservice", tunnel)
        healed = wait_for(lambda: State.RECONNECTING in recorder.seen
                          and engine.state == State.CONNECTED
                          and engine.controller.is_adapter_up(profile), 60)
        seen = " -> ".join(s.name for s in recorder.stop())
        check(healed, f"tunnel removed behind the app's back is restored ({seen})")
        check(wait_for(lambda: engine.controller.last_handshake_age(profile) is not None, 30),
              "handshake again after self-healing")

        # 4. disconnect
        engine.disconnect()
        check(engine.state == State.DISCONNECTED, f"disconnect() -> DISCONNECTED (got {engine.state.name})")
        check(wait_for(lambda: not service_exists(tunnel), 20), "disconnect removed the tunnel service")

        # 5. a server that never answers
        dead = profile.model_copy(deep=True)
        dead.profile_id = "deadbeef"
        dead.name = "CI unreachable"
        dead.peer.endpoint_port = SERVER_PORT + 7   # nothing listens here
        engine.connect(dead)
        degraded = wait_for(lambda: engine.state == State.DEGRADED, engine.NO_HANDSHAKE_GRACE + 20)
        messages = [e.payload["issue"].message for e in engine.bus.drain(500)
                    if e.type == core.EventType.ISSUE_DETECTED]
        check(degraded and any("hasn't answered" in m for m in messages),
              f"unreachable server is reported (state {engine.state.name})")
        engine.disconnect()
        check(wait_for(lambda: not service_exists(engine.controller.adapter_name(dead)), 20),
              "disconnect removed the second tunnel service")
    finally:
        engine.shutdown()
        run(WIREGUARD, "/uninstalltunnelservice", SERVER)

    report("All real tunnel checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        annotate("Real tunnel test crashed", traceback.format_exc() + "\n" + diagnostics())
        raise
