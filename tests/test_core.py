"""Unit tests for tunnel_pulse_core. They run on any OS: everything that would
touch Windows (wireguard.exe, netsh, adapters) is replaced with fakes."""

import base64
import os
import time

import pytest

import tunnel_pulse_core as core


def wg_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def conf(**overrides) -> str:
    values = {
        "PrivateKey": wg_key(),
        "Address": "10.66.0.2/32, fd00::2/128",
        "DNS": "10.64.0.1",
        "PublicKey": wg_key(),
        "AllowedIPs": "0.0.0.0/0, ::/0",
        "Endpoint": "vpn.example.com:51820",
    }
    values.update(overrides)
    iface = [f"{k} = {values[k]}" for k in ("PrivateKey", "Address", "DNS") if values[k] is not None]
    peer = [f"{k} = {values[k]}" for k in ("PublicKey", "AllowedIPs", "Endpoint") if values[k] is not None]
    return "\n".join(["# exported by a provider", "[Interface]", *iface, "", "[Peer]", *peer]) + "\n"


@pytest.fixture
def store(tmp_path):
    return core.ConfigStore(directory=tmp_path)


# ---- importing and writing .conf files ----

def test_import_round_trip(store):
    profile = store.import_wg_conf(conf(), name="home")
    assert profile.interface.address == ["10.66.0.2/32", "fd00::2/128"]
    assert profile.peer.endpoint_host == "vpn.example.com"
    assert profile.peer.endpoint_port == 51820
    assert [p.name for p in store.list_profiles()] == ["home"]

    text = profile.to_wg_conf_text()
    assert "Address = 10.66.0.2/32, fd00::2/128" in text
    assert "Endpoint = vpn.example.com:51820" in text


def test_import_accepts_notepad_byte_order_mark(store):
    text = conf().replace("# exported by a provider\n", "")   # file starts straight with [Interface]
    profile = store.import_wg_conf("\ufeff" + text, name="bom")
    assert profile.peer.endpoint_port == 51820


def test_ipv6_endpoint_keeps_brackets(store):
    profile = store.import_wg_conf(conf(Endpoint="[2001:db8::1]:51820"), name="v6")
    assert "Endpoint = [2001:db8::1]:51820" in profile.to_wg_conf_text()


def test_no_dns_line_when_profile_has_no_dns(store):
    profile = store.import_wg_conf(conf(), name="x")
    profile.interface.dns = []
    assert "DNS" not in profile.to_wg_conf_text()


@pytest.mark.parametrize("override", [
    {"Address": None},
    {"Endpoint": None},
    {"PublicKey": "not-a-key"},
    {"PrivateKey": wg_key()[:-2]},
])
def test_broken_conf_is_rejected(store, override):
    with pytest.raises(core.ProfileError):
        store.import_wg_conf(conf(**override), name="broken")


def test_corrupt_profile_file_is_set_aside(store, tmp_path):
    store.import_wg_conf(conf(), name="good")
    (tmp_path / "bad.json").write_text("{ not json", encoding="utf-8")
    assert [p.name for p in store.list_profiles()] == ["good"]


# ---- kill switch ----

def test_kill_switch_only_for_full_tunnel(store):
    full = store.import_wg_conf(conf(), name="full")
    split = store.import_wg_conf(conf(AllowedIPs="10.0.0.0/8"), name="split")
    assert core.KillSwitchManager.qualifies(full)
    assert not core.KillSwitchManager.qualifies(split)


# ---- MTU probing ----

@pytest.mark.parametrize("path_mtu, expected", [(1500, 1420), (1492, 1412), (1400, 1320), (1300, 1280)])
def test_mtu_from_path_mtu(monkeypatch, path_mtu, expected):
    monkeypatch.setattr(core, "IS_WINDOWS", True)
    optimizer = core.AdaptiveLinkOptimizer("example.com")
    monkeypatch.setattr(optimizer, "_df_ping_succeeds", lambda size: size + 28 <= path_mtu)
    assert optimizer.probe_optimal_mtu() == expected


def test_mtu_none_when_server_ignores_ping(monkeypatch):
    monkeypatch.setattr(core, "IS_WINDOWS", True)
    optimizer = core.AdaptiveLinkOptimizer("example.com")
    monkeypatch.setattr(optimizer, "_df_ping_succeeds", lambda size: False)
    assert optimizer.probe_optimal_mtu() is None


# ---- engine: health checks and self-healing ----

class FakeController:
    """Stands in for TunnelController: no wireguard.exe, no adapters."""

    def __init__(self):
        self.adapter_up = True
        self.handshake_age = 5.0
        self.start_failures = 0
        self.starts = 0
        self.locator = type("L", (), {"wg_exe": "wg.exe"})()

    def start(self, profile):
        self.starts += 1
        if self.start_failures:
            self.start_failures -= 1
            raise core.TunnelStartError("server unreachable")
        self.adapter_up = True

    def stop(self, profile):
        pass

    def is_adapter_up(self, profile):
        return self.adapter_up

    def last_handshake_age(self, profile):
        return self.handshake_age

    def adapter_name(self, profile):
        return "TunnelPulse-test"


@pytest.fixture
def engine(store):
    eng = core.TunnelPulseEngine()
    eng.controller = FakeController()
    eng.RECONNECT_BASE_DELAY = 0.001
    eng.RECONNECT_MAX_DELAY = 0.002
    eng._dns_guard.check = lambda expected: True
    eng.active_profile = store.import_wg_conf(conf(), name="p")
    eng.state = core.ConnectionState.CONNECTED
    eng._connected_at = time.time()
    yield eng
    eng.shutdown()


def warnings_from(eng):
    return [e.payload["issue"].message for e in eng.bus.drain(500)
            if e.type == core.EventType.ISSUE_DETECTED]


def test_healthy_tunnel_stays_connected(engine):
    engine._tick_health()
    assert engine.state == core.ConnectionState.CONNECTED
    assert engine.controller.starts == 0


def test_dropped_tunnel_reconnects(engine):
    engine.controller.adapter_up = False
    engine.controller.start_failures = 2
    for _ in range(3):
        engine._tick_health() if engine.state != core.ConnectionState.RECONNECTING \
            else engine._self_heal_reconnect(engine.active_profile)
    assert engine.state == core.ConnectionState.CONNECTED
    assert engine.controller.starts == 3


def test_gives_up_after_max_attempts(engine):
    engine.controller.adapter_up = False
    engine.controller.start_failures = 99
    profile = engine.active_profile
    for _ in range(engine.MAX_RECONNECT_ATTEMPTS):
        engine._self_heal_reconnect(profile)
    assert engine.state == core.ConnectionState.FAILED
    assert engine.active_profile is None
    assert any("Gave up" in w for w in warnings_from(engine))


def test_server_that_never_answers_is_reported_once(engine):
    engine.controller.handshake_age = None
    engine._connected_at = time.time() - engine.NO_HANDSHAKE_GRACE - 1
    for _ in range(5):
        engine._tick_health()
    assert engine.state == core.ConnectionState.DEGRADED
    assert sum("hasn't answered" in w for w in warnings_from(engine)) == 1

    engine.controller.handshake_age = 3.0
    engine._tick_health()
    assert engine.state == core.ConnectionState.CONNECTED


def test_no_warning_during_grace_period(engine):
    engine.controller.handshake_age = None
    engine._tick_health()
    assert engine.state == core.ConnectionState.CONNECTED
    assert warnings_from(engine) == []


def test_dns_check_skipped_without_dns_servers(monkeypatch):
    monkeypatch.setattr(core.DNSLeakGuard, "system_dns_servers", staticmethod(lambda: ["192.168.1.1"]))
    assert core.DNSLeakGuard().check([]) is True
    assert core.DNSLeakGuard().check(["10.64.0.1"]) is False


# ---- update check ----

class FakeResponse:
    def __init__(self, tag):
        self._tag = tag

    def raise_for_status(self):
        pass

    def json(self):
        return {"tag_name": self._tag}


@pytest.mark.parametrize("tag, expected", [
    ("v9.0.0", "9.0.0"),
    (f"v{core.APP_VERSION}", None),   # same version: nothing to do
    ("v0.0.1", None),                 # older release: never "update" backwards
    ("nightly", None),                # tag that isn't a version
])
def test_update_check(monkeypatch, tag, expected):
    monkeypatch.setattr(core.requests, "get", lambda *a, **k: FakeResponse(tag))
    assert core.UpdateChecker().newer_version() == expected


def test_update_check_offline(monkeypatch):
    def offline(*a, **k):
        raise core.requests.ConnectionError("no network")
    monkeypatch.setattr(core.requests, "get", offline)
    assert core.UpdateChecker().newer_version() is None


def test_version_compare_is_numeric():
    parse = core.UpdateChecker.parse_version
    assert parse("v1.10.0") > parse("1.9.9")
