from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from shortalpha.config import load_config
from shortalpha.errors import ConfigError, FixtureError
from shortalpha.providers.factory import build_universe_provider
from shortalpha.universe import load_membership

NY = ZoneInfo("America/New_York")


def _write(path: Path, rows: list[tuple[str, str]]) -> None:
    lines = ["symbol,name", *[f"{symbol},{name}" for symbol, name in rows]]
    path.write_text("\n".join(lines) + "\n")


def test_sources_are_deduped_and_class_shares_normalized(tmp_path: Path) -> None:
    sp500 = tmp_path / "sp.csv"
    nasdaq = tmp_path / "ndx.csv"
    _write(sp500, [("AAA", "Alpha"), ("brk-b", "Berkshire")])
    _write(nasdaq, [("AAA", "Alpha NDX"), ("BBB", "Beta")])
    loaded = load_membership(
        sources=("sp500", "nasdaq100"),
        files={"sp500": sp500, "nasdaq100": nasdaq},
        as_of={"sp500": date(2026, 9, 21), "nasdaq100": date(2026, 8, 9)},
    )
    assert loaded.point_in_time_membership is False
    assert loaded.list_as_of == date(2026, 8, 9)
    members = {member.symbol: member.sources for member in loaded.members}
    names = {member.symbol: member.name for member in loaded.members}
    assert members == {
        "AAA": ("nasdaq100", "sp500"),
        "BBB": ("nasdaq100",),
        "BRK.B": ("sp500",),
    }
    assert names["AAA"] == "Alpha"
    assert names["BBB"] == "Beta"
    assert names["BRK.B"] == "Berkshire"


def test_duplicate_symbol_in_one_file_fails(tmp_path: Path) -> None:
    path = tmp_path / "sp.csv"
    _write(path, [("AAA", "Alpha"), ("AAA", "Alpha Again")])
    with pytest.raises(FixtureError, match="duplicate"):
        load_membership(
            sources=("sp500",),
            files={"sp500": path},
            as_of={"sp500": date(2026, 9, 21)},
        )


def test_vendored_snapshot_is_explicitly_not_historical(repo_root: Path) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    loaded = build_universe_provider(cfg, repo_root).load(datetime(2026, 9, 27, 9, tzinfo=NY))
    assert loaded.point_in_time_membership is False
    assert loaded.list_as_of == date(2026, 8, 9)
    assert loaded.source_as_of == (
        ("nasdaq100", date(2026, 8, 9)),
        ("sp500", date(2026, 9, 21)),
    )
    members = {member.symbol: member.sources for member in loaded.members}
    assert len(members) == 518
    assert members["AAPL"] == ("nasdaq100", "sp500")
    assert "BRK.B" in members


def test_point_in_time_flag_cannot_be_enabled(repo_root: Path, tmp_path: Path) -> None:
    text = (repo_root / "config" / "default.yaml").read_text()
    path = tmp_path / "bad.yaml"
    path.write_text(
        text.replace("point_in_time_membership: false", "point_in_time_membership: true", 1)
    )
    with pytest.raises(ConfigError, match="point_in_time_membership"):
        load_config(path, root=repo_root)
