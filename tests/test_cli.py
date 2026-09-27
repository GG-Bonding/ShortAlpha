from datetime import datetime
from zoneinfo import ZoneInfo

from shortalpha.cli import main


def test_scan_rejects_a_holiday(capsys, tmp_path, repo_root) -> None:
    code = main(
        [
            "scan",
            "--config",
            str(repo_root / "config" / "default.yaml"),
            "--date",
            "2024-07-04",
            "--database",
            str(tmp_path / "scan.db"),
        ]
    )
    assert code == 1
    assert "not a trading session" in capsys.readouterr().err


def test_version_prints_package_version(capsys) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "shortalpha 0.1.0"


def test_check_reports_early_close(capsys, tmp_path, repo_root) -> None:
    code = main(
        [
            "check",
            "--config",
            str(repo_root / "config" / "default.yaml"),
            "--date",
            "2024-07-03",
            "--database",
            str(tmp_path / "check.db"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "weights: 100" in out
    assert "trading_day: true" in out
    assert "early_close: true" in out
    assert "2024-07-03T09:00:00-04:00" in out
    assert (tmp_path / "check.db").exists()


def test_check_rejects_holiday(capsys, tmp_path, repo_root) -> None:
    code = main(
        [
            "check",
            "--config",
            str(repo_root / "config" / "default.yaml"),
            "--date",
            "2024-07-04",
            "--database",
            str(tmp_path / "check.db"),
        ]
    )
    assert code == 1
    assert "trading" in capsys.readouterr().err.lower()


def test_check_without_date_uses_new_york_today(capsys, tmp_path, repo_root, monkeypatch) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 27, 9, 0, tzinfo=tz or ZoneInfo("America/New_York"))

    monkeypatch.setattr("shortalpha.cli.datetime", FrozenDateTime)
    code = main(
        [
            "check",
            "--config",
            str(repo_root / "config" / "default.yaml"),
            "--database",
            str(tmp_path / "check.db"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "2026-09-27" in out
    assert "trading_day: false" in out
