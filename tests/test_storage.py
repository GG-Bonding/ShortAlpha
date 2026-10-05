import sqlite3
from datetime import UTC, date, datetime

import pytest

from shortalpha.domain import MarketRegime, SignalRun
from shortalpha.storage import Store

TABLES = [
    "market_data",
    "news_events",
    "news_event_symbols",
    "signal_runs",
    "signal_snapshots",
    "factor_values",
    "forward_returns",
    "event_observations",
    "strategy_registry",
    "experiments",
    "evolution_decisions",
]


def _run(run_id: str) -> SignalRun:
    return SignalRun(
        run_id=run_id,
        signal_date=date(2025, 4, 10),
        signal_time=datetime(2025, 4, 10, 9, 0, tzinfo=UTC),
        timezone="America/New_York",
        config_hash="abc",
        code_version="0.1.0",
        created_at=datetime(2025, 4, 10, 13, 0, tzinfo=UTC),
        universe_size=3,
        eligible_size=2,
        scored_size=2,
        candidate_size=1,
        duration_ms=15,
        provider_errors=0,
        missing_data_count=0,
        market_regime=MarketRegime.NORMAL,
        no_trade=False,
        status="ok",
        universe_list_as_of=date(2026, 9, 1),
        point_in_time_membership=False,
        notes="",
        strategy_version="v0",
        run_mode="replay",
        event_rules_hash="rules",
    )


@pytest.fixture
def store(repo_root, tmp_path) -> Store:
    opened = Store(tmp_path / "phase1.db", repo_root / "migrations")
    yield opened
    opened.close()


def test_migrations_create_tables_and_are_idempotent(store: Store) -> None:
    store.migrate()
    names = {
        row[0] for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert set(TABLES) <= names


def test_run_round_trip_keeps_two_run_ids(store: Store) -> None:
    store.insert_run(_run("run-a"))
    store.insert_run(_run("run-b"))
    loaded = store.get_run("run-a")
    assert loaded is not None
    assert loaded.signal_date == date(2025, 4, 10)
    assert loaded.market_regime is MarketRegime.NORMAL
    assert loaded.no_trade is False
    assert loaded.point_in_time_membership is False
    assert {row[0] for row in store.conn.execute("SELECT run_id FROM signal_runs")} == {
        "run-a",
        "run-b",
    }


def test_duplicate_run_id_is_rejected(store: Store) -> None:
    store.insert_run(_run("run-a"))
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_run(_run("run-a"))


def test_snapshot_requires_run_and_round_trips(store: Store) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_snapshot("missing", "{}", "hash", datetime.now(UTC))
    created = datetime(2025, 4, 10, 13, 1, tzinfo=UTC)
    store.insert_run(_run("run-a"))
    store.insert_snapshot("run-a", '{"signal_date":"2025-04-10"}', "hash-a", created)
    loaded = store.get_snapshot("run-a")
    assert loaded == ('{"signal_date":"2025-04-10"}', "hash-a")


def _seed(store: Store) -> None:
    store.insert_run(_run("run-a"))
    store.insert_snapshot(
        "run-a",
        "{}",
        "hash",
        datetime(2025, 4, 10, 13, 1, tzinfo=UTC),
    )
    store.conn.execute(
        """
        INSERT INTO market_data (
            symbol, session_date, bar_type, open, high, low, close, volume,
            event_time, published_at, available_at, source
        ) VALUES ('AAA', '2025-04-09', 'daily', 1, 1, 1, 1, 1,
                  '2025-04-09T20:00:00+00:00', '2025-04-09T20:00:00+00:00',
                  '2025-04-09T20:00:00+00:00', 'fixture')
        """
    )
    store.conn.execute(
        """
        INSERT INTO news_events (
            id, headline, summary, source, event_time, published_at, available_at, raw_json
        ) VALUES ('n1', 'h', '', 'fixture', '2025-04-10T12:00:00+00:00',
                  '2025-04-10T12:00:00+00:00', '2025-04-10T12:00:00+00:00', '{}')
        """
    )
    store.conn.execute("INSERT INTO news_event_symbols (news_id, symbol) VALUES ('n1', 'AAA')")
    store.conn.execute(
        """
        INSERT INTO factor_values (
            run_id, symbol, factor, raw_value, normalized_value, score, available,
            reasons_json, risks_json, details_json
        ) VALUES ('run-a', 'AAA', 'momentum', 0, 0, 0, 1, '[]', '[]', '{}')
        """
    )
    store.conn.execute(
        """
        INSERT INTO forward_returns (
            run_id, symbol, horizon, code_version
        ) VALUES ('run-a', 'AAA', 1, '0.1.0')
        """
    )
    store.conn.execute(
        """
        INSERT INTO event_observations (
            run_id, symbol, news_id, rule_id, label, family, source,
            published_at, available_at, content_sha256, rules_sha256,
            signed, direction, importance, freshness
        ) VALUES (
            'run-a', 'AAA', 'n1', 'earnings_beat', 'Earnings beat', 'earnings',
            'fixture', '2025-04-10T12:00:00+00:00', '2025-04-10T12:00:00+00:00',
            'abc', 'rules', 0.1, 1, 0.9, 1
        )
        """
    )
    store.conn.execute(
        """
        INSERT INTO strategy_registry (
            strategy_version, recorded_at, role, parent_version, config_hash,
            event_rules_hash, change_json
        ) VALUES ('v0', '2025-04-10T13:00:00+00:00', 'official', NULL, 'hash', 'rules', '{}')
        """
    )
    store.conn.execute(
        """
        INSERT INTO experiments (
            experiment_id, created_at, baseline_version, candidate_version, run_mode,
            baseline_run_ids, change_json, dev_end, validation_start, validation_end,
            config_hash, event_rules_hash
        ) VALUES (
            'exp-1', '2025-04-10T13:00:00+00:00', 'v0', 'v1', 'replay',
            '[]', '{}', '2025-04-01', '2025-04-02', '2025-04-03', 'hash', 'rules'
        )
        """
    )
    store.conn.execute(
        """
        INSERT INTO evolution_decisions (
            decision_id, experiment_id, recorded_at, as_of, action, terminal,
            shadow_run_ids, evidence_json
        ) VALUES (
            'dec-1', 'exp-1', '2025-04-10T13:00:00+00:00', '2025-04-10T13:00:00+00:00',
            'KEEP_CURRENT', 1, '[]', '{}'
        )
        """
    )
    store.conn.commit()


@pytest.mark.parametrize("table", TABLES)
def test_business_tables_reject_update_and_delete(store: Store, table: str) -> None:
    _seed(store)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.conn.execute(f"UPDATE {table} SET rowid = rowid")
    store.conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.conn.execute(f"DELETE FROM {table}")
    store.conn.rollback()
