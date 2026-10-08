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
        _require_identity(run)
        self._execute(
            """
            INSERT INTO signal_runs (
                run_id, signal_date, signal_time, timezone, config_hash, code_version,
                created_at, universe_size, eligible_size, scored_size, candidate_size,
                duration_ms, provider_errors, missing_data_count, market_regime, no_trade,
                status, universe_list_as_of, point_in_time_membership, notes,
                strategy_version, run_mode, event_rules_hash, experiment_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            strategy_version=row["strategy_version"] or "",
            run_mode=row["run_mode"] or "",
            event_rules_hash=row["event_rules_hash"] or "",
            experiment_id=row["experiment_id"] or "",
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
        _require_identity(run)
        try:
            self.conn.execute(
                """
                INSERT INTO signal_runs (
                    run_id, signal_date, signal_time, timezone, config_hash, code_version,
                    created_at, universe_size, eligible_size, scored_size, candidate_size,
                    duration_ms, provider_errors, missing_data_count, market_regime, no_trade,
                    status, universe_list_as_of, point_in_time_membership, notes,
                    strategy_version, run_mode, event_rules_hash, experiment_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                for event in factor.events:
                    self.conn.execute(
                        """
                        INSERT INTO event_observations (
                            run_id, symbol, news_id, rule_id, label, family, source,
                            published_at, available_at, content_sha256, rules_sha256,
                            signed, direction, importance, freshness, reaction
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run.run_id,
                            symbol,
                            event.news_id,
                            event.rule_id,
                            event.label,
                            event.family,
                            event.source,
                            event.published_at.isoformat(),
                            event.available_at.isoformat(),
                            event.content_sha256,
                            event.rules_sha256,
                            event.signed,
                            event.direction,
                            event.importance,
                            event.freshness,
                            event.reaction,
                        ),
                    )
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            raise

    def insert_forward_return(
        self,
        *,
        run_id: str,
        symbol: str,
        horizon: int,
        entry_session: date,
        entry_price: float,
        exit_session: date,
        exit_price: float,
        stock_return: float,
        spy_return: float,
        excess_return: float,
        mae: float,
        mfe: float,
        code_version: str,
        label_available_at: datetime,
    ) -> None:
        if horizon not in {1, 2, 3, 5}:
            raise ValueError("horizon must be 1, 2, 3, or 5")
        ensure_aware("label_available_at", label_available_at)
        self._execute(
            """
            INSERT INTO forward_returns (
                run_id, symbol, horizon, entry_session_date, entry_price,
                exit_session_date, exit_price, stock_return, spy_return,
                excess_return, mae, mfe, code_version, label_available_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                symbol,
                horizon,
                entry_session.isoformat(),
                entry_price,
                exit_session.isoformat(),
                exit_price,
                stock_return,
                spy_return,
                excess_return,
                mae,
                mfe,
                code_version,
                label_available_at.isoformat(),
            ),
        )

    def forward_for(self, run_id: str, *, as_of: datetime | None = None) -> list[sqlite3.Row]:
        if as_of is not None:
            ensure_aware("as_of", as_of)
        rows = list(
            self.conn.execute(
                """
                SELECT * FROM forward_returns
                WHERE run_id = ?
                ORDER BY symbol, horizon
                """,
                (run_id,),
            )
        )
        if as_of is None:
            return rows
        visible: list[sqlite3.Row] = []
        for row in rows:
            stamp = row["label_available_at"]
            if stamp is None:
                continue
            if datetime.fromisoformat(stamp) <= as_of:
                visible.append(row)
        return visible

    def insert_registry(
        self,
        *,
        strategy_version: str,
        recorded_at: datetime,
        role: str,
        parent_version: str | None,
        config_hash: str,
        event_rules_hash: str,
        change_json: str,
        rules_json: str,
    ) -> None:
        ensure_aware("recorded_at", recorded_at)
        if role not in {"official", "candidate", "retired"}:
            raise ValueError("role must be official, candidate, or retired")
        self._execute(
            """
            INSERT INTO strategy_registry (
                strategy_version, recorded_at, role, parent_version, config_hash,
                event_rules_hash, change_json, rules_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_version,
                recorded_at.isoformat(),
                role,
                parent_version,
                config_hash,
                event_rules_hash,
                change_json,
                rules_json,
            ),
        )

    def latest_registry(self, strategy_version: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM strategy_registry
            WHERE strategy_version = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            (strategy_version,),
        ).fetchone()

    def official_version(self, default: str) -> str:
        row = self.conn.execute(
            """
            SELECT strategy_version FROM strategy_registry
            WHERE role = 'official'
            ORDER BY recorded_at DESC, strategy_version DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return default
        return str(row["strategy_version"])

    def insert_experiment(
        self,
        *,
        experiment_id: str,
        created_at: datetime,
        baseline_version: str,
        candidate_version: str,
        run_mode: str,
        baseline_run_ids: str,
        change_json: str,
        dev_end: date,
        validation_start: date,
        validation_end: date,
        config_hash: str,
        event_rules_hash: str,
        shadow_starts_at: datetime,
        training_cutoff_at: datetime,
        baseline_rules_json: str,
        promotion_json: str,
    ) -> None:
        ensure_aware("created_at", created_at)
        ensure_aware("shadow_starts_at", shadow_starts_at)
        ensure_aware("training_cutoff_at", training_cutoff_at)
        self._execute(
            """
            INSERT INTO experiments (
                experiment_id, created_at, baseline_version, candidate_version, run_mode,
                baseline_run_ids, change_json, dev_end, validation_start, validation_end,
                config_hash, event_rules_hash, shadow_starts_at, training_cutoff_at,
                baseline_rules_json, promotion_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                created_at.isoformat(),
                baseline_version,
                candidate_version,
                run_mode,
                baseline_run_ids,
                change_json,
                dev_end.isoformat(),
                validation_start.isoformat(),
                validation_end.isoformat(),
                config_hash,
                event_rules_hash,
                shadow_starts_at.isoformat(),
                training_cutoff_at.isoformat(),
                baseline_rules_json,
                promotion_json,
            ),
        )

    def experiments(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM experiments ORDER BY created_at, experiment_id")
        )

    def experiment(self, experiment_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()

    def insert_decision(
        self,
        *,
        decision_id: str,
        experiment_id: str,
        recorded_at: datetime,
        as_of: datetime,
        action: str,
        terminal: bool,
        shadow_run_ids: str,
        evidence_json: str,
    ) -> None:
        ensure_aware("recorded_at", recorded_at)
        ensure_aware("as_of", as_of)
        if action not in {"KEEP_CURRENT", "PROMOTE"}:
            raise ValueError("action must be KEEP_CURRENT or PROMOTE")
        self._execute(
            """
            INSERT INTO evolution_decisions (
                decision_id, experiment_id, recorded_at, as_of, action, terminal,
                shadow_run_ids, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                experiment_id,
                recorded_at.isoformat(),
                as_of.isoformat(),
                action,
                int(terminal),
                shadow_run_ids,
                evidence_json,
            ),
        )

    def decisions_for(self, experiment_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM evolution_decisions
                WHERE experiment_id = ?
                ORDER BY recorded_at, decision_id
                """,
                (experiment_id,),
            )
        )

    def all_runs(self) -> list[SignalRun]:
        rows = self.conn.execute(
            "SELECT run_id FROM signal_runs ORDER BY signal_date, created_at, run_id"
        ).fetchall()
        loaded = [self.get_run(row["run_id"]) for row in rows]
        return [run for run in loaded if run is not None]

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

    def insert_scan_inputs(self, run_id: str, inputs_json: str, created_at: datetime) -> None:
        ensure_aware("created_at", created_at)
        if not inputs_json:
            raise ValueError("scan inputs are required")
        self._execute(
            """
            INSERT INTO scan_inputs (run_id, inputs_json, created_at)
            VALUES (?, ?, ?)
            """,
            (run_id, inputs_json, created_at.isoformat()),
        )

    def get_scan_inputs(self, run_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT inputs_json FROM scan_inputs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["inputs_json"])

    def _execute(self, sql: str, params: tuple[object, ...]) -> None:
        try:
            self.conn.execute(sql, params)
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise


def _require_identity(run: SignalRun) -> None:
    if not run.strategy_version or not run.run_mode or not run.event_rules_hash:
        raise ValueError("strategy_version, run_mode, and event_rules_hash are required")


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
        run.strategy_version,
        run.run_mode,
        run.event_rules_hash,
        run.experiment_id,
    )
