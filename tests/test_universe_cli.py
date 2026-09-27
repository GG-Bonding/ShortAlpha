import json
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from shortalpha.calendar import NYSECalendar
from shortalpha.cli import main

NY = ZoneInfo("America/New_York")


def test_universe_command_prints_bias_and_overlap(capsys, repo_root: Path) -> None:
    code = main(["universe", "--config", str(repo_root / "config" / "default.yaml")])
    assert code == 0
    output = capsys.readouterr().out
    assert "point_in_time_membership: false" in output
    assert "list_as_of: 2026-08-09" in output
    assert "symbols: 518" in output
    assert "AAPL  nasdaq100,sp500" in output


def test_universe_date_filters_with_fixture_bars(capsys, repo_root: Path, tmp_path: Path) -> None:
    calendar = NYSECalendar()
    session = date(2024, 6, 20)
    days = [calendar.shift(session, -offset) for offset in range(20, 0, -1)]
    days.append(session)
    bars = []
    for day in days:
        close_at = calendar.session_close(day)
        same_day = day == session
        bars.append(
            {
                "symbol": "AAA",
                "session_date": day.isoformat(),
                "open": 1 if same_day else 10,
                "high": 1 if same_day else 10,
                "low": 1 if same_day else 10,
                "close": 1 if same_day else 10,
                "volume": 1 if same_day else 5_000_000,
                "event_time": close_at.isoformat(),
                "published_at": close_at.isoformat(),
                "available_at": close_at.isoformat(),
                "source": "fixture",
            }
        )
    market_path = tmp_path / "bars.json"
    market_path.write_text(json.dumps({"bars": bars}))
    sp500 = tmp_path / "sp.csv"
    nasdaq = tmp_path / "ndx.csv"
    sp500.write_text("symbol,name\nAAA,Alpha\n")
    nasdaq.write_text("symbol,name\nAAA,Alpha\n")
    text = (repo_root / "config" / "default.yaml").read_text()
    text = text.replace("fixtures/market/sample_bars.json", str(market_path))
    text = text.replace("fixtures/universe/sp500.csv", str(sp500))
    text = text.replace("fixtures/universe/nasdaq100.csv", str(nasdaq))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(text)
    code = main(["universe", "--config", str(config_path), "--date", "2024-06-20"])
    assert code == 0
    output = capsys.readouterr().out
    assert "eligible: 1" in output
    assert "missing: 0" in output
    assert "AAA  eligible  price=10 avg_dollar_volume=50000000" in output
    assert datetime.combine(session, time(9, 0), tzinfo=NY).isoformat() in output


def test_universe_rejects_a_holiday(capsys, repo_root: Path) -> None:
    code = main(
        ["universe", "--config", str(repo_root / "config" / "default.yaml"), "--date", "2024-07-04"]
    )
    assert code == 1
    assert "trading" in capsys.readouterr().err.lower()
