"""SQLite store. Research rows are insert-only."""

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from shortalpha.domain import FactorResult, MarketRegime, SignalRun, ensure_aware


class Store:
    def __init__(self, path: Path | str, migrations: Path) -> None:
        self.path = Path(path)
        self.migrations = Path(migrations)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(self.migrations.glob("*.sql")):
            if path.name in applied:
                continue
            self.conn.executescript(path.read_text())
            self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (path.name, datetime.now(UTC).isoformat()),
            )
        self.conn.commit()

    def insert_run(self, run: SignalRun) -> None:
        self._execute(
            """
            INSERT INTO signal_runs (
                run_id, signal_date, signal_time, timezone, config_hash, code_version,
                created_at, universe_size, eligible_size, scored_size, candidate_size,
                duration_ms, provider_errors, missing_data_count, market_regime, no_trade,
                status, universe_list_as_of, point_in_time_membership, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _run_params(run),
        )

    def get_run(self, run_id: str) -> SignalRun | None:
        row = self.conn.execute(
            "SELECT * FROM signal_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        regime = row["market_regime"]
        list_as_of = row["universe_list_as_of"]
        return SignalRun(
            run_id=row["run_id"],
            signal_date=date.fromisoformat(row["signal_date"]),
            signal_time=datetime.fromisoformat(row["signal_time"]),
            timezone=row["timezone"],
            config_hash=row["config_hash"],
            code_version=row["code_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            universe_size=row["universe_size"],
            eligible_size=row["eligible_size"],
            scored_size=row["scored_size"],
            candidate_size=row["candidate_size"],
            duration_ms=row["duration_ms"],
            provider_errors=row["provider_errors"],
            missing_data_count=row["missing_data_count"],
            market_regime=None if regime is None else MarketRegime(regime),
            no_trade=bool(row["no_trade"]),
            status=row["status"],
            universe_list_as_of=None if list_as_of is None else date.fromisoformat(list_as_of),
            point_in_time_membership=bool(row["point_in_time_membership"]),
            notes=row["notes"],
        )

    def insert_snapshot(
        self,
        run_id: str,
        snapshot_json: str,
        snapshot_hash: str,
        created_at: datetime,
    ) -> None:
        ensure_aware("created_at", created_at)
        self._execute(
            """
            INSERT INTO signal_snapshots (run_id, snapshot_json, snapshot_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, snapshot_json, snapshot_hash, created_at.isoformat()),
        )

    def insert_signal(
        self,
        run: SignalRun,
        snapshot_json: str,
        snapshot_hash: str,
        factors: list[tuple[str, FactorResult]],
    ) -> None:
        ensure_aware("created_at", run.created_at)
        try:
            self.conn.execute(
                """
                INSERT INTO signal_runs (
                    run_id, signal_date, signal_time, timezone, config_hash, code_version,
                    created_at, universe_size, eligible_size, scored_size, candidate_size,
                    duration_ms, provider_errors, missing_data_count, market_regime, no_trade,
                    status, universe_list_as_of, point_in_time_membership, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _run_params(run),
            )
            self.conn.execute(
                """
                INSERT INTO signal_snapshots (run_id, snapshot_json, snapshot_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (run.run_id, snapshot_json, snapshot_hash, run.created_at.isoformat()),
            )
            for symbol, factor in factors:
                self.conn.execute(
                    """
                    INSERT INTO factor_values (
                        run_id, symbol, factor, raw_value, normalized_value, score,
                        available, reasons_json, risks_json, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        symbol,
                        factor.name,
                        factor.raw_value,
                        factor.normalized_value,
                        factor.score,
                        int(factor.available),
                        json.dumps(list(factor.reasons)),
                        json.dumps(list(factor.risks)),
                        json.dumps(list(factor.details)),
                    ),
                )
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            raise

    def runs_on(self, signal_date: date) -> list[SignalRun]:
        rows = self.conn.execute(
            """
            SELECT run_id FROM signal_runs
            WHERE signal_date = ?
            ORDER BY created_at, run_id
            """,
            (signal_date.isoformat(),),
        ).fetchall()
        loaded = [self.get_run(row["run_id"]) for row in rows]
        return [run for run in loaded if run is not None]

    def get_snapshot(self, run_id: str) -> tuple[str, str] | None:
        row = self.conn.execute(
            "SELECT snapshot_json, snapshot_hash FROM signal_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return (row["snapshot_json"], row["snapshot_hash"])

    def _execute(self, sql: str, params: tuple[object, ...]) -> None:
        try:
            self.conn.execute(sql, params)
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise


def _run_params(run: SignalRun) -> tuple[object, ...]:
    return (
        run.run_id,
        run.signal_date.isoformat(),
        run.signal_time.isoformat(),
        run.timezone,
        run.config_hash,
        run.code_version,
        run.created_at.isoformat(),
        run.universe_size,
        run.eligible_size,
        run.scored_size,
        run.candidate_size,
        run.duration_ms,
        run.provider_errors,
        run.missing_data_count,
        None if run.market_regime is None else run.market_regime.value,
        int(run.no_trade),
        run.status,
        None if run.universe_list_as_of is None else run.universe_list_as_of.isoformat(),
        int(run.point_in_time_membership),
        run.notes,
    )
