from datetime import time
from pathlib import Path

import pytest

from shortalpha.config import config_hash, load_config
from shortalpha.errors import ConfigError
from shortalpha.event_rules import event_rules_content_hash


def test_default_config_loads_signal_time_as_clock(repo_root: Path) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    assert cfg.signal.time == time(9, 0)
    assert cfg.signal.timezone == "America/New_York"
    assert cfg.weights.momentum == 25
    assert cfg.weights.volume == 20
    assert cfg.weights.event == 25
    assert cfg.weights.relative_strength == 15
    assert cfg.weights.price_action == 15
    assert cfg.ranking.top_n == 3
    assert cfg.ranking.long_threshold == 80
    assert cfg.ranking.watch_threshold == 70
    assert cfg.universe.filters.min_price == 5
    assert cfg.universe.filters.min_avg_dollar_volume_20d == 50_000_000
    assert cfg.event.freshness_lambda == 0.05
    assert cfg.price_action.gap_penalty_start == 0.03
    assert cfg.alpaca.adjustment == "raw"
    assert "SECRET" not in (repo_root / "config" / "default.yaml").read_text().upper()
    assert "API_KEY" not in (repo_root / "config" / "default.yaml").read_text()


def test_config_hash_is_stable_and_includes_event_rule_bytes(repo_root: Path) -> None:
    first = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    second = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    digest = event_rules_content_hash(repo_root / first.event.rules)
    assert config_hash(first, digest) == config_hash(second, digest)
    assert len(config_hash(first, digest)) == 64
    assert config_hash(first, "a" * 64) != config_hash(first, "b" * 64)


def _write_variant(repo_root: Path, tmp_path: Path, old: str, new: str) -> Path:
    text = (repo_root / "config" / "default.yaml").read_text()
    assert old in text
    path = tmp_path / "variant.yaml"
    path.write_text(text.replace(old, new, 1))
    return path


def test_weight_sum_must_equal_100(repo_root: Path, tmp_path: Path) -> None:
    path = _write_variant(repo_root, tmp_path, "momentum: 25", "momentum: 24")
    with pytest.raises(ConfigError, match="100"):
        load_config(path, root=repo_root)


def test_momentum_components_must_sum_to_one(repo_root: Path, tmp_path: Path) -> None:
    path = _write_variant(repo_root, tmp_path, "w5: 0.45", "w5: 0.40")
    with pytest.raises(ConfigError, match="momentum"):
        load_config(path, root=repo_root)


def test_price_action_parts_must_sum_to_weight(repo_root: Path, tmp_path: Path) -> None:
    path = _write_variant(repo_root, tmp_path, "break_bonus: 2.5", "break_bonus: 3")
    with pytest.raises(ConfigError, match="price_action"):
        load_config(path, root=repo_root)


def test_horizons_are_fixed(repo_root: Path, tmp_path: Path) -> None:
    path = _write_variant(repo_root, tmp_path, "horizons: [1, 2, 3, 5]", "horizons: [1, 3, 5]")
    with pytest.raises(ConfigError, match="horizons"):
        load_config(path, root=repo_root)


def test_missing_fixture_file_fails(repo_root: Path, tmp_path: Path) -> None:
    path = _write_variant(
        repo_root,
        tmp_path,
        "universe: fixtures/universe/sample.json",
        "universe: fixtures/universe/missing.json",
    )
    with pytest.raises(ConfigError, match="missing"):
        load_config(path, root=repo_root)


def test_watch_threshold_must_be_below_long(repo_root: Path, tmp_path: Path) -> None:
    path = _write_variant(repo_root, tmp_path, "watch_threshold: 70", "watch_threshold: 90")
    with pytest.raises(ConfigError, match="watch"):
        load_config(path, root=repo_root)
