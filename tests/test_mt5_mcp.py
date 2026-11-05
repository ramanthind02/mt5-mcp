"""Unit tests. They mock MetaTrader and do not need a live terminal."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from mt5_mcp import account, connection, market, profile, registry, snapshot, tester
from mt5_mcp.config import password_env_name
from mt5_mcp.connection import session
from mt5_mcp.parsers import parse_compile_log, parse_tester_report
from mt5_mcp.paths import launch_key
from mt5_mcp.registry import TerminalRecord


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("MT5_MCP_HOME", str(tmp_path))
    session.record = None
    session.connected = False
    session.ready = False
    yield tmp_path
    session.record = None
    session.connected = False
    session.ready = False


def test_password_env_name():
    assert password_env_name("darwinex-demo") == "DARWINEX_DEMO_PASSWORD"


def test_registry_merges_origin_and_hides_password(home: Path, monkeypatch):
    def explode(self, *args, **kwargs):
        raise AssertionError(f"resolve follows junctions: {self}")

    monkeypatch.setattr(Path, "resolve", explode)
    digest = "a" * 32
    folder = home / "app" / "MetaQuotes" / "Terminal" / digest
    folder.mkdir(parents=True)
    (folder / "origin.txt").write_bytes("C:\\MT5\\Broker".encode("utf-16"))
    (home / "terminals.toml").write_text(
        """
[terminals.demo]
path = 'C:\\MT5\\Broker\\terminal64.exe'
login = 42
server = "Demo-Server"
""".strip(),
        encoding="utf-8",
    )
    (home / "secrets.env").write_text("DEMO_PASSWORD=super-secret\n", encoding="utf-8")
    payload = registry.list_terminals(appdata=home / "app", running_paths=set())
    ids = [item["id"] for item in payload["terminals"]]
    assert ids == ["demo"]
    assert payload["terminals"][0]["login"] == 42
    assert payload["terminals"][0]["data_dir"] == str(folder)
    assert payload["terminals"][0]["password_configured"] is True
    assert "super-secret" not in json.dumps(payload)


def test_discovered_terminal_uses_hash_id(home: Path):
    digest = "b" * 32
    folder = home / "app" / "MetaQuotes" / "Terminal" / digest
    folder.mkdir(parents=True)
    (folder / "origin.txt").write_text("D:\\Other", encoding="utf-8")
    payload = registry.list_terminals(
        appdata=home / "app", config={}, running_paths=set(), include_discovered=True
    )
    assert payload["terminals"][0]["id"] == digest
    assert payload["terminals"][0]["source"] == "discovered"


def test_compile_log_and_report_parse():
    parsed = parse_compile_log(
        "MyEA.mq5(10,5) : error 123: undeclared identifier\nResult: 1 errors, 0 warnings\n"
    )
    assert parsed["ok"] is False
    assert parsed["errors"][0]["code"] == 123
    report = parse_tester_report(
        "<table><tr><td>Total Net Profit:</td><td>1200.50</td></tr>"
        "<tr><td>Profit Factor:</td><td>1.40</td></tr></table>"
    )
    assert report["summary"]["net_profit"] == "1200.50"
    assert report["summary"]["profit_factor"] == "1.40"


def test_compare_reports(tmp_path: Path):
    base = tmp_path / "base.htm"
    cand = tmp_path / "cand.htm"
    base.write_text("<tr><td>Total Net Profit:</td><td>100</td></tr>", encoding="utf-8")
    cand.write_text("<tr><td>Total Net Profit:</td><td>80</td></tr>", encoding="utf-8")
    diff = tester.compare_reports(str(base), str(cand))
    row = diff["diffs"][0]
    assert row["delta"] == -20
    assert row["pct"] == -20


def test_tester_ini_refuses_live_chart():
    refused = tester.validate_tester_config("[Tester]\nShutdownTerminal=1\n[Chart1]\nExpert=Live\n")
    assert refused["ok"] is False
    missing = tester.validate_tester_config("[Tester]\nVisual=0\n")
    assert missing["ok"] is False


def test_write_tester_config_is_headless(tmp_path: Path):
    source = tmp_path / "EA.mq5"
    source.write_text("input double Lots = 0.1; // size\n", encoding="utf-8")
    written = tester.write_tester_config(
        expert="EA",
        symbol="EURUSD",
        period="H1",
        source=str(source),
        path=str(tmp_path / "tester.ini"),
    )
    assert written["ok"] is True
    text = Path(written["path"]).read_text(encoding="utf-16")
    assert "ShutdownTerminal=1" in text
    assert "Visual=0" in text
    assert "Lots=0.1||0.1||0||0.1||N" in text
    assert tester.validate_tester_config(text)["ok"] is True


def _activate(tmp_path: Path) -> None:
    exe = tmp_path / "terminal64.exe"
    exe.write_bytes(b"")
    data = tmp_path / "data"
    (data / "MQL5" / "Experts").mkdir(parents=True)
    (data / "Tester" / "logs").mkdir(parents=True)
    session.record = TerminalRecord(
        id="demo",
        name="demo",
        exe=exe,
        install=tmp_path,
        data_dir=data,
        login=42,
        server="Demo",
        enabled=True,
        source="config",
        password_env=None,
        password_configured=True,
        running=False,
    )
    session.connected = True
    session.ready = True


def test_run_backtest_launches_only_validated_ini(tmp_path: Path):
    _activate(tmp_path)
    ini = tmp_path / "ok.ini"
    ini.write_text("[Tester]\nShutdownTerminal=1\nExpert=EA\n", encoding="utf-8")
    seen = {}

    class Proc:
        def __init__(self, cmd):
            seen["cmd"] = cmd
            self.pid = 99
            self._done = False

        def wait(self, timeout=None):
            self._done = True
            return 0

        def poll(self):
            return 0 if self._done else None

    result = tester.run_backtest(str(ini), popen=Proc, processes=[])
    assert result["returncode"] == 0
    assert "error" not in result
    assert seen["cmd"][0] == str(tmp_path / "terminal64.exe")
    assert seen["cmd"][1].startswith("/config:")

    live = tmp_path / "live.ini"
    live.write_text("[Tester]\nShutdownTerminal=1\n[Charts]\nProfile=default\n", encoding="utf-8")
    refused = tester.run_backtest(str(live), popen=Proc)
    assert "error" in refused
    assert "cmd" not in seen or seen["cmd"][1].endswith("ok.ini")


def test_stop_backtest_only_tracks_our_process():
    assert "error" in tester.stop_backtest()


def test_copy_rates_pages_oldest_first(tmp_path: Path, monkeypatch):
    _activate(tmp_path)

    class Fake:
        def symbol_select(self, symbol, visible):
            return True

        def copy_rates_from_pos(self, symbol, timeframe, pos, count):
            if pos == 0:
                return [{"time": 200, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]
            if pos == 1:
                return [{"time": 100, "open": 1, "high": 1, "low": 1, "close": 1}]
            return []

        def last_error(self):
            return (1, "ok")

    monkeypatch.setattr(market, "import_mt5", lambda: Fake())
    dest = tmp_path / "rates.csv"
    result = market.copy_rates("EURUSD", "M1", save_to=str(dest), pause=0, chunk=1)
    assert result["count"] == 2
    assert "preview" in result
    text = dest.read_text(encoding="utf-8")
    assert text.index("1970-01-01 00:01:40") < text.index("1970-01-01 00:03:20")


def test_probe_history_saves_daily_bars(tmp_path: Path, monkeypatch):
    _activate(tmp_path)

    class Fake:
        def symbol_select(self, symbol, visible):
            return True

        def symbols_get(self, group=None):
            return [SimpleNamespace(name="EURUSD"), SimpleNamespace(name="GBPUSD")]

        def copy_rates_from_pos(self, symbol, timeframe, pos, count):
            if pos > 0:
                return []
            base = 1_700_000_000 if symbol == "EURUSD" else 1_800_000_000
            return [
                {
                    "time": base,
                    "open": 1,
                    "high": 2,
                    "low": 0.5,
                    "close": 1.5,
                    "tick_volume": 10,
                    "spread": 2,
                    "real_volume": 0,
                },
                {
                    "time": base + 86400,
                    "open": 1.5,
                    "high": 2,
                    "low": 1,
                    "close": 1.8,
                    "tick_volume": 11,
                    "spread": 2,
                    "real_volume": 0,
                },
            ]

        def last_error(self):
            return (1, "ok")

    monkeypatch.setattr(market, "import_mt5", lambda: Fake())
    result = market.probe_history("D1", save=True)
    assert result["with_bars"] == 2
    survey = json.loads(Path(result["survey_path"]).read_text(encoding="utf-8"))
    assert survey["symbols"][0]["bars"] == 2
    csv_path = Path(survey["symbols"][0]["path"])
    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "time,open,high,low,close,tick_volume,spread,real_volume"


def test_copy_ticks_requires_all_history_flag(tmp_path: Path):
    _activate(tmp_path)
    result = market.copy_ticks("EURUSD")
    assert "all_history" in result["error"]


def test_capture_profile_stores_every_symbol_and_summary_stays_small(tmp_path: Path, monkeypatch):
    _activate(tmp_path)

    class Info:
        def __init__(self, name):
            self.name = name

        def _asdict(self):
            return {
                "name": self.name,
                "digits": 5,
                "trade_tick_value": 1.25,
                "trade_tick_size": 0.00001,
            }

    class SessionRow:
        def _asdict(self):
            return {"from": 0, "to": 86400}

    class Fake:
        def symbols_get(self, group=None):
            return [SimpleNamespace(name="EURUSD"), SimpleNamespace(name="GBPUSD")]

        def symbol_info(self, name):
            return Info(name)

        def symbol_select(self, symbol, visible):
            return True

        def symbol_info_session(self, symbol, day, index):
            return SessionRow() if index == 0 else None

        def symbol_info_tick(self, symbol):
            return SimpleNamespace(time=int(datetime.now(UTC).timestamp()))

        def terminal_info(self):
            return SimpleNamespace(
                company="Broker", name="MT5", build=5000, path="p", data_path="d"
            )

        def account_info(self):
            return SimpleNamespace(
                login=42,
                server="Demo",
                currency="USD",
                leverage=100,
                trade_mode=0,
                company="Broker",
                name="Demo",
            )

        def last_error(self):
            return (1, "ok")

    monkeypatch.setattr(profile, "import_mt5", lambda: Fake())
    monkeypatch.setattr(connection, "import_mt5", lambda: Fake())
    captured = profile.capture_profile()
    assert captured["symbol_count"] == 2
    assert "trade_tick_value" not in captured
    document = json.loads(Path(captured["path"]).read_text(encoding="utf-8"))
    assert set(document["symbols"]) == {"EURUSD", "GBPUSD"}
    assert document["symbols"]["EURUSD"]["digits"] == 5
    assert document["symbols"]["EURUSD"]["sessions"]["sessions"]["0"]
    summary = profile.read_profile()
    assert summary["symbol_count"] == 2
    assert "digits" not in summary
    one = profile.read_profile(symbol="EURUSD")
    assert one["symbol"]["trade_tick_value"] == 1.25


def test_use_terminal_never_logs_in(home: Path, monkeypatch):
    exe = home / "terminal64.exe"
    exe.write_bytes(b"")
    digest = "c" * 32
    folder = home / "app" / "MetaQuotes" / "Terminal" / digest
    folder.mkdir(parents=True)
    (folder / "origin.txt").write_text(str(home), encoding="utf-8")
    (home / "terminals.toml").write_text(
        f"""
[terminals.demo]
path = '{exe}'
login = 42
server = "Demo"
""".strip(),
        encoding="utf-8",
    )
    (home / "secrets.env").write_text("DEMO_PASSWORD=super-secret\n", encoding="utf-8")

    class Fake:
        def __init__(self):
            self.calls = []

        def initialize(self, path, timeout=0):
            return True

        def shutdown(self):
            return None

        def account_info(self):
            return SimpleNamespace(
                login=7,
                server="Other",
                currency="USD",
                leverage=100,
                trade_mode=2,
                company="Broker",
                name="Live",
            )

        def login(self, login, password=None, server=None):
            self.calls.append(password)
            return True

        def last_error(self):
            return (1, "ok")

    fake = Fake()
    monkeypatch.setattr(connection, "import_mt5", lambda: fake)
    result = connection.use_terminal("demo", appdata=home / "app")
    assert fake.calls == []
    assert result["ready"] is False
    assert result["login"] == 7
    assert "terminal window" in result["error"]
    assert "super-secret" not in json.dumps(result)


def test_registry_hides_unregistered_until_added(home: Path):
    digest = "e" * 32
    folder = home / "app" / "MetaQuotes" / "Terminal" / digest
    folder.mkdir(parents=True)
    install = home / "Broker"
    install.mkdir()
    (folder / "origin.txt").write_text(str(install), encoding="utf-8")
    hidden = registry.list_terminals(appdata=home / "app", running_paths=set())
    assert hidden["count"] == 0
    added = registry.add_terminal(
        terminal_id=digest,
        name="broker-demo",
        role="demo",
        login=42,
        server="Demo",
        appdata=home / "app",
    )
    assert added["added"] == "broker-demo"
    assert added["files_deleted"] is False
    listed = registry.list_terminals(appdata=home / "app", running_paths=set())
    assert [item["id"] for item in listed["terminals"]] == ["broker-demo"]
    assert listed["terminals"][0]["role"] == "demo"
    assert listed["terminals"][0]["login"] == 42
    removed = registry.remove_terminal("broker-demo")
    assert removed["files_deleted"] is False
    assert install.exists()
    assert folder.exists()
    assert registry.list_terminals(appdata=home / "app", running_paths=set())["count"] == 0


def test_launch_terminal_skips_a_running_install_and_starts_any_path(tmp_path: Path):
    running = tmp_path / "live" / "terminal64.exe"
    stopped = tmp_path / "demo" / "terminal64.exe"
    running.parent.mkdir()
    stopped.parent.mkdir()
    running.write_bytes(b"")
    stopped.write_bytes(b"")
    started: list[str] = []
    skipped = registry.launch_terminal(
        path=str(running),
        running_paths={launch_key(running)},
        runner=lambda exe: started.append(str(exe)),
    )
    assert skipped["already_running"] is True
    assert started == []
    launched = registry.launch_terminal(
        path=str(stopped),
        running_paths=set(),
        runner=lambda exe: started.append(str(exe)),
    )
    assert launched["launched"] is True
    assert started == [str(stopped)]


def _junction(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["cmd", "/c", "mklink", "/J", str(link), str(target)])


@pytest.mark.skipif(
    os.name != "nt", reason="MetaTrader data folders follow the Windows launch path"
)
def test_junction_and_target_keep_separate_data_folders(home: Path):
    target = home / "install"
    link = home / "shortcut"
    _junction(link, target)
    (target / "terminal64.exe").write_bytes(b"")
    app = home / "app"
    real_hash = "a" * 32
    link_hash = "b" * 32
    real = app / "MetaQuotes" / "Terminal" / real_hash
    linked = app / "MetaQuotes" / "Terminal" / link_hash
    real.mkdir(parents=True)
    linked.mkdir(parents=True)
    (real / "origin.txt").write_text(str(target), encoding="utf-8")
    (linked / "origin.txt").write_text(str(link), encoding="utf-8")
    shortcut = link / "terminal64.exe"
    (home / "terminals.toml").write_text(
        f"[terminals.demo]\npath = '{shortcut}'\n",
        encoding="utf-8",
    )
    listed = registry.list_terminals(appdata=app, running_paths=set())
    demo = listed["terminals"][0]
    assert demo["data_dir"] == str(linked)
    assert "junction" in demo["path_warning"]
    assert str(target / "terminal64.exe") in demo["path_warning"]
    refused = registry.add_terminal(path=str(shortcut), name="nope", appdata=app)
    assert "junction" in refused["error"]
    launched = registry.launch_terminal(
        path=str(shortcut), running_paths=set(), runner=lambda exe: None
    )
    assert "junction" in launched["error"]
    attached = connection.use_terminal("demo", appdata=app)
    assert "junction" in attached["error"]
    assert session.record is None

    (home / "terminals.toml").write_text(
        f"[terminals.demo]\npath = '{target / 'terminal64.exe'}'\n",
        encoding="utf-8",
    )
    real_entry = registry.list_terminals(appdata=app, running_paths=set())["terminals"][0]
    assert real_entry["data_dir"] == str(real)
    assert real_entry["path_warning"] is None


def test_run_backtest_refuses_an_open_terminal_and_a_stale_report(tmp_path: Path, monkeypatch):
    _activate(tmp_path)
    exe = tmp_path / "terminal64.exe"
    ini = tmp_path / "fresh.ini"
    ini.write_text("[Tester]\nShutdownTerminal=1\nReport=fresh\n", encoding="utf-8")
    (tmp_path / "data" / "old-report.htm").write_text(
        "<tr><td>Total Net Profit:</td><td>1</td></tr>",
        encoding="utf-8",
    )

    def explode(self, *args, **kwargs):
        raise AssertionError(f"resolve follows junctions: {self}")

    monkeypatch.setattr(Path, "resolve", explode)
    seen: dict = {"calls": 0}

    class Proc:
        def __init__(self, cmd):
            seen["calls"] += 1
            seen["cmd"] = cmd
            self.pid = 99
            self._done = False

        def wait(self, timeout=None):
            self._done = True
            return 0

        def poll(self):
            return 0 if self._done else None

    missing = tester.run_backtest(str(ini), popen=Proc, processes=[])
    assert seen["calls"] == 1
    assert seen["cmd"][0] == str(exe)
    assert missing["report_path"] is None
    assert "fresh.htm" in missing["error"]
    assert "old-report" not in json.dumps(missing)

    focused = tester.run_backtest(
        str(ini),
        popen=Proc,
        processes=[{"pid": 9, "exe": str(exe)}],
    )
    assert focused["already_running"] is True
    assert seen["calls"] == 1

    monkeypatch.setattr("mt5_mcp.paths.image_key", lambda path: "same-image")
    other = tester.run_backtest(
        str(ini),
        popen=Proc,
        processes=[{"pid": 5, "exe": r"C:\Program Files\Darwinex MetaTrader 5\terminal64.exe"}],
    )
    assert "different data folder" in other["error"]
    assert seen["calls"] == 1

    report = tmp_path / "data" / "fresh.htm"
    report.write_text("<tr><td>Total Net Profit:</td><td>2</td></tr>", encoding="utf-8")
    written = tester.run_backtest(str(ini), popen=Proc, processes=[])
    assert written["report_path"] == str(report)
    assert "error" not in written


@pytest.mark.skipif(
    os.name != "nt", reason="MetaTrader data folders follow the Windows launch path"
)
def test_run_backtest_refuses_a_junction_install(tmp_path: Path):
    _activate(tmp_path)
    target = tmp_path / "install"
    link = tmp_path / "shortcut"
    _junction(link, target)
    (target / "terminal64.exe").write_bytes(b"")
    session.record.exe = link / "terminal64.exe"
    session.record.install = link
    ini = tmp_path / "ok.ini"
    ini.write_text("[Tester]\nShutdownTerminal=1\nReport=fresh\n", encoding="utf-8")
    called = []
    refused = tester.run_backtest(str(ini), popen=lambda cmd: called.append(cmd), processes=[])
    assert called == []
    assert "junction" in refused["error"]
    assert str(target / "terminal64.exe") in refused["error"]


def test_account_health_is_read_only(tmp_path: Path, monkeypatch):
    _activate(tmp_path)

    class Pos:
        def _asdict(self):
            return {"ticket": 1, "symbol": "EURUSD", "volume": 0.1, "profit": 3.5}

    class Order:
        def _asdict(self):
            return {"ticket": 9, "symbol": "EURUSD", "type": 2, "volume_current": 0.2}

    class Fake:
        def account_info(self):
            return SimpleNamespace(
                login=42,
                server="Demo",
                trade_mode=0,
                currency="USD",
                leverage=100,
                balance=10000,
                equity=10003.5,
                margin=50,
                margin_free=9953.5,
                margin_level=20007,
                profit=3.5,
                company="Broker",
                name="Demo",
            )

        def terminal_info(self):
            return SimpleNamespace(trade_allowed=True)

        def positions_get(self):
            return [Pos()]

        def orders_get(self):
            return [Order()]

        def last_error(self):
            return (1, "ok")

    monkeypatch.setattr("mt5_mcp.account.import_mt5", lambda: Fake())
    health = account.account_health()
    assert health["balance"] == 10000
    assert health["equity"] == 10003.5
    assert health["margin_free"] == 9953.5
    assert health["trade_allowed"] is True
    assert health["position_count"] == 1
    assert health["orders"][0]["ticket"] == 9
    assert account.positions()["count"] == 1
    assert account.orders()["count"] == 1


def _spec(**overrides) -> dict:
    base = {
        "spread": 1,
        "spread_float": True,
        "swap_long": -3.5,
        "swap_short": 1.2,
        "swap_mode": 1,
        "trade_stops_level": 0,
        "trade_contract_size": 100000,
        "filling_mode": 1,
        "sessions": {"sessions": {"1": [{"from": 0, "to": 86400}]}},
    }
    base.update(overrides)
    return base


def test_read_report_returns_every_closed_trade(tmp_path: Path):
    html = """
    <table>
    <tr><td>Total Net Profit:</td><td>50.00</td></tr>
    <tr><td colspan="13">Deals</td></tr>
    <tr>
    <td>Time</td><td>Deal</td><td>Symbol</td><td>Type</td><td>Direction</td>
    <td>Volume</td><td>Price</td><td>Order</td><td>Commission</td><td>Swap</td>
    <td>Profit</td><td>Balance</td><td>Comment</td>
    </tr>
    <tr>
    <td>2024.01.01 00:00:00</td><td>1</td><td></td><td>balance</td><td></td>
    <td></td><td></td><td></td><td>0.00</td><td>0.00</td><td>10000.00</td>
    <td>10000.00</td><td></td>
    </tr>
    <tr>
    <td>2024.01.02 10:15:00</td><td>2</td><td>EURUSD</td><td>buy</td><td>in</td>
    <td>0.10</td><td>1.10450</td><td>2</td><td>0.00</td><td>0.00</td><td>0.00</td>
    <td>10000.00</td><td></td>
    </tr>
    <tr>
    <td>2024.01.02 12:15:00</td><td>3</td><td>EURUSD</td><td>sell</td><td>out</td>
    <td>0.10</td><td>1.10550</td><td>3</td><td>-0.70</td><td>0.00</td><td>90.00</td>
    <td>10089.30</td><td>tp</td>
    </tr>
    <tr>
    <td>2024.01.03 09:00:00</td><td>4</td><td>EURUSD</td><td>sell</td><td>in</td>
    <td>0.20</td><td>1.11000</td><td>4</td><td>0.00</td><td>0.00</td><td>0.00</td>
    <td>10089.30</td><td></td>
    </tr>
    <tr>
    <td>2024.01.03 11:00:00</td><td>5</td><td>EURUSD</td><td>buy</td><td>out</td>
    <td>0.20</td><td>1.11200</td><td>5</td><td>0.00</td><td>-1.50</td><td>-40.00</td>
    <td>10047.80</td><td>sl</td>
    </tr>
    <tr><td colspan="11">Orders</td></tr>
    <tr>
    <td>Time</td><td>Order</td><td>Symbol</td><td>Type</td><td>Volume</td><td>Price</td>
    <td>S / L</td><td>T / P</td><td>Time</td><td>State</td><td>Comment</td>
    </tr>
    <tr>
    <td>2024.01.02 10:15:00</td><td>2</td><td>EURUSD</td><td>buy</td><td>0.10</td><td>1.10450</td>
    <td></td><td></td><td>2024.01.02 10:15:00</td><td>filled</td><td></td>
    </tr>
    </table>
    """
    report = tmp_path / "report.htm"
    report.write_text(html, encoding="utf-8")
    parsed = tester.read_report(str(report))
    assert parsed["trade_count"] == 2
    assert parsed["summary"]["net_profit"] == "50.00"
    long_trade, short_trade = parsed["trades"]
    assert long_trade["side"] == "long"
    assert long_trade["entry_hour"] == 10
    assert long_trade["holding_seconds"] == 7200
    assert long_trade["profit"] == 90
    assert long_trade["comment"] == "tp"
    assert long_trade["commission"] == pytest.approx(-0.7)
    assert long_trade["volume"] == pytest.approx(0.1)
    assert short_trade["side"] == "short"
    assert short_trade["entry_hour"] == 9
    assert short_trade["profit"] == -40
    assert short_trade["swap"] == pytest.approx(-1.5)
    assert parsed["breakdown"]["long"]["expectancy"] == 90
    assert parsed["breakdown"]["short"]["expectancy"] == -40
    assert parsed["breakdown"]["holding_seconds"]["median"] == 7200
    assert {row["hour"] for row in parsed["breakdown"]["by_entry_hour"]} == {9, 10}
    saved = json.loads(Path(parsed["trades_path"]).read_text(encoding="utf-8"))
    assert len(saved["trades"]) == 2

    partial = parse_tester_report(
        "<tr><td>Time</td><td>Deal</td><td>Symbol</td><td>Type</td><td>Direction</td>"
        "<td>Volume</td><td>Price</td><td>Order</td><td>Commission</td><td>Swap</td>"
        "<td>Profit</td><td>Balance</td><td>Comment</td></tr>"
        "<tr><td>2024.01.02 10:00:00</td><td>2</td><td>EURUSD</td><td>buy</td><td>in</td>"
        "<td>0.20</td><td>1.10</td><td>2</td><td>0</td><td>0</td><td>0</td><td>10000</td><td></td></tr>"
        "<tr><td>2024.01.02 11:00:00</td><td>3</td><td>EURUSD</td><td>sell</td><td>out</td>"
        "<td>0.10</td><td>1.11</td><td>3</td><td>0</td><td>0</td><td>30</td><td>10030</td><td>p1</td></tr>"
        "<tr><td>2024.01.02 13:00:00</td><td>4</td><td>EURUSD</td><td>sell</td><td>out</td>"
        "<td>0.10</td><td>1.12</td><td>4</td><td>0</td><td>0</td><td>10</td><td>10040</td><td>p2</td></tr>"
    )
    assert [row["entry_time"] for row in partial["trades"]] == [
        "2024.01.02 10:00:00",
        "2024.01.02 10:00:00",
    ]
    assert [row["holding_seconds"] for row in partial["trades"]] == [3600, 10800]
    assert partial["breakdown"]["long"]["expectancy"] == 20

    bare = parse_tester_report(
        "<tr><td>2024.01.02 10:00:00</td><td>2</td><td>EURUSD</td><td>buy</td><td>in</td>"
        "<td>1</td><td>1.1</td><td>2</td><td>0</td><td>0</td><td>0</td><td>10000</td><td></td></tr>"
        "<tr><td>2024.01.02 11:00:00</td><td>3</td><td>EURUSD</td><td>sell</td><td>out</td>"
        "<td>1</td><td>1.2</td><td>3</td><td>0</td><td>0</td><td>5</td><td>10005</td><td>tp</td></tr>"
    )
    assert bare["trades"][0]["side"] == "long"
    assert bare["trades"][0]["profit"] == 5


def test_compare_profiles_flags_transfer_fields(home: Path):
    from mt5_mcp.config import profiles_dir

    def save(terminal_id: str, symbols: dict) -> None:
        document = {
            "captured_at": "2026-01-01T00:00:00+00:00",
            "account": {"server": terminal_id},
            "symbol_count": len(symbols),
            "symbols": symbols,
        }
        root = profiles_dir()
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{terminal_id}.json").write_text(json.dumps(document), encoding="utf-8")

    same = _spec()
    save("a", {"EURUSD": _spec(), "USDJPY": same, "XAUUSD": _spec()})
    save(
        "b",
        {
            "EURUSD": _spec(
                spread=15,
                swap_long=-8,
                swap_mode=2,
                trade_stops_level=5,
                trade_contract_size=10000,
                filling_mode=2,
                sessions={"sessions": {"0": [{"from": 0, "to": 86400}]}},
            ),
            "USDJPY": same,
        },
    )
    save("c", {"EURUSD": _spec(), "USDJPY": same})
    assert "two" in profile.compare_profiles(["a"])["error"]
    result = profile.compare_profiles(["a", "b"])
    assert result["unchanged_symbols"] == 1
    assert result["changed_symbols"] == 1
    assert result["missing_symbols"] == 1
    by_symbol = {item["symbol"]: item for item in result["diffs"]}
    assert "USDJPY" not in by_symbol
    assert by_symbol["XAUUSD"]["missing_from"] == ["b"]
    fields = by_symbol["EURUSD"]["fields"]
    assert "spread_float" not in fields
    assert "swap_short" not in fields
    assert fields["spread"]["delta"] == 14
    assert fields["contract_size"]["delta"] == -90000
    assert fields["filling"]["a"]["modes"] == ["fok"]
    assert fields["filling"]["b"]["modes"] == ["ioc"]
    assert fields["swap_mode"]["a"]["name"] == "points"
    assert fields["swap_mode"]["b"]["name"] == "currency_symbol"
    assert fields["sessions"]["a"] == "Mon 00:00-24:00"
    assert fields["sessions"]["b"] == "Sun 00:00-24:00"
    assert fields["stops_level"]["b"] == 5
    narrowed = profile.compare_profiles(["a", "b"], symbols=["EURUSD"])
    assert [item["symbol"] for item in narrowed["diffs"]] == ["EURUSD"]
    three = profile.compare_profiles(["a", "b", "c"], symbols=["EURUSD"])
    assert set(three["diffs"][0]["fields"]["spread"]) == {"a", "b", "c"}


def test_run_backtest_snapshots_sources_and_report(tmp_path: Path):
    _activate(tmp_path)
    experts = tmp_path / "data" / "MQL5" / "Experts"
    include = tmp_path / "data" / "MQL5" / "Include"
    include.mkdir(parents=True)
    (include / "Helper.mqh").write_text("int Helper(){return 1;}\n", encoding="utf-8")
    (include / "Other.mqh").write_text("int Other(){return 0;}\n", encoding="utf-8")
    (experts / "Signal.mqh").write_text("#include <Missing.mqh>\n", encoding="utf-8")
    source = experts / "EA.mq5"
    source.write_text(
        '#include <Helper.mqh>\n#include "Signal.mqh"\ninput double Lots = 0.1;\n',
        encoding="utf-8",
    )
    (experts / "EA.ex5").write_bytes(b"ex5")
    report = tmp_path / "data" / "run1.htm"
    report.write_text("<html></html>", encoding="utf-8")
    ini = tmp_path / "ok.ini"
    ini.write_text(
        "[Tester]\nShutdownTerminal=1\nExpert=EA\nSymbol=EURUSD\nPeriod=H1\n"
        "FromDate=2024.01.01\nToDate=2024.12.31\nReport=run1\n\n"
        "[TesterInputs]\nLots=0.2||0.2||0||0.2||N\n",
        encoding="utf-8",
    )

    class Proc:
        def __init__(self, cmd):
            self.pid = 7
            self._done = False

        def wait(self, timeout=None):
            self._done = True
            return 0

        def poll(self):
            return 0 if self._done else None

    result = tester.run_backtest(str(ini), popen=Proc, processes=[])
    assert result["report_path"] == str(report)
    assert "error" not in result
    manifest = snapshot.read_snapshot(result["snapshot_path"])
    assert Path(manifest["report_path"]).name == "run1.htm"
    assert manifest["inputs"]["Lots"] == "0.2"
    assert manifest["symbol"] == "EURUSD"
    names = {item["path"] for item in manifest["files"]}
    assert names == {"Experts/EA.mq5", "Experts/EA.ex5", "Experts/Signal.mqh", "Include/Helper.mqh"}
    assert manifest["missing_includes"] == ["Missing.mqh"]
    copied = Path(manifest["directory"]) / "sources" / "Experts" / "EA.mq5"
    assert copied.read_text(encoding="utf-8").startswith("#include")
    listed = snapshot.list_snapshots()
    assert listed["count"] == 1
    assert listed["snapshots"][0]["report_path"] == str(report)
    reread = tester.read_report(str(report))
    assert reread["snapshot_path"] == result["snapshot_path"]
    assert reread["trade_count"] == 0


def test_journal_scan_flags_init_history_and_rejects():
    scanned = tester.scan_journal(
        [
            {"message": "EURUSD,H1: initialization failed with code 1"},
            {"message": "EURUSD,H1: history not synchronized"},
            {"message": "order buy 0.10 EURUSD rejected"},
            {"message": "final balance 10000.00 USD"},
            {"message": "test passed"},
            {"message": "EURUSD,H1: history synchronized"},
        ]
    )
    assert {hit["kind"] for hit in scanned["blocking"]} == {"init", "history", "reject"}
    assert scanned["ok"] is False
    assert {note["kind"] for note in scanned["notes"]} == {"balance", "passed", "history_ok"}


def _compile_ok(path, timeout_sec=300):
    Path(path).with_suffix(".ex5").write_bytes(b"bin")
    return {
        "ok": True,
        "result_errors": 0,
        "result_warnings": 0,
        "errors": [],
        "warnings": [],
    }


def _passed_log(folder: Path, name: str) -> str:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("CS\t0\t00:00:01\tTester\ttest passed\n", encoding="utf-8")
    return str(path)


def test_smoke_test_stops_when_compile_fails(tmp_path: Path, monkeypatch):
    _activate(tmp_path)
    source = tmp_path / "data" / "MQL5" / "Experts" / "EA.mq5"
    source.write_text("input double Lots = 0.1;\n", encoding="utf-8")
    monkeypatch.setattr(
        tester,
        "compile_source",
        lambda path, timeout_sec=300: {
            "ok": False,
            "errors": [{"message": "undeclared"}],
            "result_errors": 1,
            "result_warnings": 0,
            "warnings": [],
        },
    )
    result = tester.smoke_test(str(source), "EURUSD")
    assert result["ok"] is False
    assert result["stage"] == "compile"


def test_smoke_test_runs_one_day_and_reads_the_journal(tmp_path: Path, monkeypatch):
    _activate(tmp_path)
    source = tmp_path / "data" / "MQL5" / "Experts" / "EA.mq5"
    source.write_text("input double Lots = 0.1;\n", encoding="utf-8")
    monkeypatch.setattr(tester, "compile_source", _compile_ok)
    seen = {}

    def fake_run(config, timeout_sec=1800, popen=None, processes=None):
        seen["ini"] = Path(config).read_text(encoding="utf-16")
        seen["timeout"] = timeout_sec
        log = _passed_log(tmp_path / "data" / "Tester" / "logs", "ok.log")
        return {"returncode": 0, "tester_log": log, "report_path": None}

    monkeypatch.setattr(tester, "run_backtest", fake_run)
    result = tester.smoke_test(str(source), "EURUSD", date="2024.06.03", timeout_sec=90)
    assert "FromDate=2024.06.03" in seen["ini"]
    assert "ToDate=2024.06.03" in seen["ini"]
    assert "Expert=EA.ex5" in seen["ini"]
    assert seen["timeout"] == 90
    assert result["ok"] is True
    assert result["stage"] == "done"
    assert result["attempts"] == 1
    assert result["copied"] is False
    assert result["journal"]["blocking"] == []
    assert result["journal"]["notes"][0]["kind"] == "passed"


def test_smoke_test_retries_unsynced_history_once(tmp_path: Path, monkeypatch):
    _activate(tmp_path)
    source = tmp_path / "data" / "MQL5" / "Experts" / "EA.mq5"
    source.write_text("input double Lots = 0.1;\n", encoding="utf-8")
    monkeypatch.setattr(tester, "compile_source", _compile_ok)
    calls = {"n": 0}

    def fake_run(config, timeout_sec=1800, popen=None, processes=None):
        calls["n"] += 1
        log = tmp_path / "data" / "Tester" / "logs" / f"{calls['n']}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        if calls["n"] == 1:
            log.write_text(
                "CS\t0\t00:00:01\tTester\tEURUSD,H1: history not synchronized\n", encoding="utf-8"
            )
        else:
            log.write_text("CS\t0\t00:00:02\tTester\ttest passed\n", encoding="utf-8")
        return {"returncode": 0, "tester_log": str(log)}

    monkeypatch.setattr(tester, "run_backtest", fake_run)
    result = tester.smoke_test(str(source), "EURUSD", date="2024.06.03")
    assert calls["n"] == 2
    assert result["attempts"] == 2
    assert result["ok"] is True


def test_smoke_test_does_not_retry_an_init_failure(tmp_path: Path, monkeypatch):
    _activate(tmp_path)
    source = tmp_path / "data" / "MQL5" / "Experts" / "EA.mq5"
    source.write_text("input double Lots = 0.1;\n", encoding="utf-8")
    monkeypatch.setattr(tester, "compile_source", _compile_ok)
    calls = {"n": 0}

    def fake_run(config, timeout_sec=1800, popen=None, processes=None):
        calls["n"] += 1
        log = tmp_path / "data" / "Tester" / "logs" / "init.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "CS\t0\t00:00:01\tTester\tinitialization failed with code 1\n", encoding="utf-8"
        )
        return {"returncode": 0, "tester_log": str(log)}

    monkeypatch.setattr(tester, "run_backtest", fake_run)
    result = tester.smoke_test(str(source), "EURUSD")
    assert calls["n"] == 1
    assert result["ok"] is False
    assert result["stage"] == "journal"
    assert result["journal"]["blocking"][0]["kind"] == "init"


def test_smoke_test_copies_an_external_source_into_experts(tmp_path: Path, monkeypatch):
    _activate(tmp_path)
    external = tmp_path / "src" / "EA.mq5"
    external.parent.mkdir()
    external.write_text("input int X = 1;\n", encoding="utf-8")
    monkeypatch.setattr(tester, "compile_source", _compile_ok)
    seen = {}

    def fake_run(config, timeout_sec=1800, popen=None, processes=None):
        seen["expert"] = next(
            line
            for line in Path(config).read_text(encoding="utf-16").splitlines()
            if line.startswith("Expert=")
        )
        log = _passed_log(tmp_path / "data" / "Tester" / "logs", "ext.log")
        return {"returncode": 0, "tester_log": log}

    monkeypatch.setattr(tester, "run_backtest", fake_run)
    result = tester.smoke_test(str(external), "GBPUSD", date="2024.06.03")
    assert result["ok"] is True
    assert result["copied"] is True
    assert (tmp_path / "data" / "MQL5" / "Experts" / "EA.ex5").is_file()
    assert (tmp_path / "data" / "MQL5" / "Experts" / "EA.mq5").is_file()
    assert seen["expert"] == "Expert=EA.ex5"


def test_server_tool_surface():
    from mt5_mcp.server import mcp

    tools = list(mcp._tool_manager.list_tools())
    names = {tool.name for tool in tools}
    assert names == {
        "list_terminals",
        "add_terminal",
        "remove_terminal",
        "launch_terminal",
        "use_terminal",
        "terminal_status",
        "disconnect",
        "account_health",
        "positions",
        "orders",
        "capture_profile",
        "read_profile",
        "list_profiles",
        "compare_profiles",
        "list_symbols",
        "symbol_info",
        "symbol_tick",
        "select_symbol",
        "copy_rates",
        "probe_history",
        "history_coverage",
        "extend_history",
        "copy_ticks",
        "list_experts",
        "compile",
        "read_inputs",
        "write_tester_config",
        "run_backtest",
        "smoke_test",
        "read_report",
        "compare_reports",
        "tail_log",
        "stop_backtest",
        "list_snapshots",
        "read_snapshot",
    }
    assert "login" not in names

    resources = {str(item.uri) for item in mcp._resource_manager.list_resources()}
    assert resources == {
        "mt5://getting_started",
        "mt5://terminals",
        "mt5://market_data_guide",
        "mt5://account_guide",
        "mt5://backtest_guide",
        "mt5://timeframes",
        "mt5://tester_models",
    }
    prompts = {item.name for item in mcp._prompt_manager.list_prompts()}
    assert prompts == {
        "connect_terminal",
        "analyze_market_data",
        "review_account",
        "backtest_expert",
        "compare_backtests",
    }
    from mt5_mcp.guides import _timeframes_text

    frames = _timeframes_text()
    assert "| H1 | 16385 |" in frames
    assert "| H1 | 60 |" not in frames
    forbidden = re.compile(
        r"\b(order_send|order_check|TRADE_ACTION_|history_deals_get|history_orders_get|close_position)\b"
    )
    root = Path(__file__).resolve().parents[1] / "src"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if forbidden.search(text):
            offenders.append(str(path))
    assert offenders == []
