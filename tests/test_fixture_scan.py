"""File fixtures through scan, rank, and explain.

AAA's news lands on a minute boundary and an IEX print confirms it.
BBB's only minute is hours earlier in the same session.
CCC has no minute price in the file.
"""

import json
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from shortalpha.calendar import NYSECalendar
from shortalpha.cli import main
from shortalpha.paths import project_root
from shortalpha.providers.fixture_market import FixtureMarketDataProvider

NY = ZoneInfo("America/New_York")
SESSION = date(2024, 6, 18)
AAA_NEWS = datetime(2024, 6, 18, 8, 30, tzinfo=NY)
BBB_NEWS = datetime(2024, 6, 17, 15, 59, 30, tzinfo=NY)
BBB_PRINT = datetime(2024, 6, 17, 9, 31, tzinfo=NY)
CCC_NEWS = datetime(2024, 6, 17, 16, 5, tzinfo=NY)
SIGNAL = datetime(2024, 6, 18, 9, 0, tzinfo=NY)


def test_fixture_files_reach_scan_rank_and_explain(tmp_path: Path, capsys) -> None:
    market_path = tmp_path / "market.json"
    _write_inputs(tmp_path, market_path)
    loaded = FixtureMarketDataProvider.from_json(market_path)
    exact = loaded.price_at("AAA", AAA_NEWS, SIGNAL)
    assert exact is not None
    assert exact.available_at == AAA_NEWS
    assert exact.price == 100
    assert loaded.price_at("CCC", CCC_NEWS, SIGNAL) is None

    config = _config(tmp_path)
    database = tmp_path / "scan.db"
    scan_code = main(
        [
            "scan",
            "--date",
            SESSION.isoformat(),
            "--config",
            str(config),
            "--database",
            str(database),
        ]
    )
    captured = capsys.readouterr()
    assert scan_code == 0, captured.err
    assert "#1 AAA" in captured.out
    assert "LONG_CANDIDATE" in captured.out
    assert "not consolidated" in captured.out
    assert "NO_TRADE:\nfalse" in captured.out

    aaa = _explain(config, database, "AAA", capsys)
    bbb = _explain(config, database, "BBB", capsys)
    ccc = _explain(config, database, "CCC", capsys)
    assert "Signal: LONG_CANDIDATE" in aaa
    assert "Veto:" not in aaa
    assert "0 seconds before the news" in aaa
    assert "not consolidated" in aaa
    age = int((BBB_NEWS - BBB_PRINT).total_seconds())
    assert f"{age} seconds before the news" in bbb
    assert "post-event baseline is approximate" in bbb
    assert "post-event baseline is approximate" in ccc
    assert "seconds before the news" not in ccc


def _explain(config: Path, database: Path, symbol: str, capsys) -> str:
    code = main(
        [
            "explain",
            symbol,
            "--date",
            SESSION.isoformat(),
            "--config",
            str(config),
            "--database",
            str(database),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return captured.out


def _config(tmp: Path) -> Path:
    text = (project_root() / "config" / "default.yaml").read_text()
    text = text.replace("  universe: file\n", "  universe: fixture\n", 1)
    replacements = {
        "fixtures/market/sample_bars.json": str(tmp / "market.json"),
        "fixtures/premarket/sample_premarket.json": str(tmp / "premarket.json"),
        "fixtures/news/sample_news.json": str(tmp / "news.json"),
        "fixtures/universe/sample.json": str(tmp / "universe.json"),
    }
    for old, new in replacements.items():
        text = text.replace(old, new, 1)
    path = tmp / "config.yaml"
    path.write_text(text)
    return path


def _write_inputs(tmp: Path, market_path: Path) -> None:
    calendar = NYSECalendar()
    days = [calendar.shift(SESSION, -offset) for offset in range(30, 0, -1)]
    stock_closes = [88.0] * (len(days) - 6) + [88.0, 90.0, 93.0, 96.0, 98.0, 100.0]
    bars = []
    for symbol in ("AAA", "BBB", "CCC"):
        for index, day in enumerate(days):
            volume = 4_000_000 if index == len(days) - 1 else 1_000_000
            bars.append(_bar(symbol, day, stock_closes[index], volume))
    for symbol in ("SPY", "QQQ", "XLK", "XLF", "XLE"):
        for day in days:
            bars.append(_bar(symbol, day, 100.0, 1_000_000))
    market_path.write_text(
        json.dumps(
            {
                "bars": bars,
                "prices": [
                    {
                        "symbol": "AAA",
                        "price": 100.0,
                        "available_at": AAA_NEWS.isoformat(),
                        "consolidated": True,
                    },
                    {
                        "symbol": "BBB",
                        "price": 90.0,
                        "available_at": BBB_PRINT.isoformat(),
                        "consolidated": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp / "news.json").write_text(
        json.dumps(
            {
                "items": [
                    _news("aaa", "AAA", "AAA earnings beat", AAA_NEWS),
                    _news("bbb", "BBB", "BBB earnings beat", BBB_NEWS),
                    _news("ccc", "CCC", "CCC earnings beat", CCC_NEWS),
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp / "premarket.json").write_text(
        json.dumps({"windows": [_iex(symbol) for symbol in ("AAA", "BBB", "CCC")]}),
        encoding="utf-8",
    )
    (tmp / "universe.json").write_text(
        json.dumps(
            {
                "list_as_of": "2026-09-01",
                "point_in_time_membership": False,
                "symbols": [
                    {"symbol": symbol, "sources": ["sp500"]} for symbol in ("AAA", "BBB", "CCC")
                ],
            }
        ),
        encoding="utf-8",
    )


def _bar(symbol: str, day: date, close: float, volume: float) -> dict[str, object]:
    stamp = datetime.combine(day, time(16, 0), tzinfo=NY).isoformat()
    return {
        "symbol": symbol,
        "session_date": day.isoformat(),
        "open": close,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": volume,
        "event_time": stamp,
        "published_at": stamp,
        "available_at": stamp,
        "source": "fixture",
    }


def _news(item_id: str, symbol: str, headline: str, stamp: datetime) -> dict[str, object]:
    text = stamp.isoformat()
    return {
        "id": item_id,
        "symbols": [symbol],
        "headline": headline,
        "summary": "",
        "source": "fixture",
        "event_time": text,
        "published_at": text,
        "available_at": text,
        "url": None,
    }


def _iex(symbol: str) -> dict[str, object]:
    stamp = SIGNAL.isoformat()
    return {
        "symbol": symbol,
        "session_date": SESSION.isoformat(),
        "available": False,
        "volume": None,
        "last_price": 102.0,
        "high": 102.4,
        "low": 101.6,
        "event_time": stamp,
        "published_at": stamp,
        "available_at": stamp,
        "reason": "IEX feed is not consolidated premarket; premarket_volume_available is false",
        "price_consolidated": False,
    }
