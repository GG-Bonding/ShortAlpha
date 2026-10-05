ALTER TABLE signal_runs ADD COLUMN strategy_version TEXT;
ALTER TABLE signal_runs ADD COLUMN run_mode TEXT;
ALTER TABLE signal_runs ADD COLUMN event_rules_hash TEXT;

ALTER TABLE forward_returns ADD COLUMN label_available_at TEXT;

CREATE TABLE IF NOT EXISTS event_observations (
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    news_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    label TEXT NOT NULL,
    family TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    rules_sha256 TEXT NOT NULL,
    signed REAL NOT NULL,
    direction REAL NOT NULL,
    importance REAL NOT NULL,
    freshness REAL NOT NULL,
    reaction REAL,
    PRIMARY KEY (run_id, symbol, news_id, rule_id),
    FOREIGN KEY (run_id) REFERENCES signal_runs (run_id)
);

CREATE TABLE IF NOT EXISTS strategy_registry (
    strategy_version TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('official', 'candidate', 'retired')),
    parent_version TEXT,
    config_hash TEXT NOT NULL,
    event_rules_hash TEXT NOT NULL,
    change_json TEXT NOT NULL,
    PRIMARY KEY (strategy_version, recorded_at)
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    baseline_version TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    baseline_run_ids TEXT NOT NULL,
    change_json TEXT NOT NULL,
    dev_end TEXT NOT NULL,
    validation_start TEXT NOT NULL,
    validation_end TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    event_rules_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_decisions (
    decision_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('KEEP_CURRENT', 'PROMOTE')),
    terminal INTEGER NOT NULL,
    shadow_run_ids TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    FOREIGN KEY (experiment_id) REFERENCES experiments (experiment_id)
);

CREATE INDEX IF NOT EXISTS idx_runs_version ON signal_runs (strategy_version, run_mode, signal_date);
CREATE INDEX IF NOT EXISTS idx_forward_label ON forward_returns (run_id, label_available_at);

CREATE TRIGGER IF NOT EXISTS event_observations_no_update
BEFORE UPDATE ON event_observations
BEGIN
    SELECT RAISE(ABORT, 'event_observations is immutable');
END;

CREATE TRIGGER IF NOT EXISTS event_observations_no_delete
BEFORE DELETE ON event_observations
BEGIN
    SELECT RAISE(ABORT, 'event_observations is immutable');
END;

CREATE TRIGGER IF NOT EXISTS strategy_registry_no_update
BEFORE UPDATE ON strategy_registry
BEGIN
    SELECT RAISE(ABORT, 'strategy_registry is immutable');
END;

CREATE TRIGGER IF NOT EXISTS strategy_registry_no_delete
BEFORE DELETE ON strategy_registry
BEGIN
    SELECT RAISE(ABORT, 'strategy_registry is immutable');
END;

CREATE TRIGGER IF NOT EXISTS experiments_no_update
BEFORE UPDATE ON experiments
BEGIN
    SELECT RAISE(ABORT, 'experiments is immutable');
END;

CREATE TRIGGER IF NOT EXISTS experiments_no_delete
BEFORE DELETE ON experiments
BEGIN
    SELECT RAISE(ABORT, 'experiments is immutable');
END;

CREATE TRIGGER IF NOT EXISTS evolution_decisions_no_update
BEFORE UPDATE ON evolution_decisions
BEGIN
    SELECT RAISE(ABORT, 'evolution_decisions is immutable');
END;

CREATE TRIGGER IF NOT EXISTS evolution_decisions_no_delete
BEFORE DELETE ON evolution_decisions
BEGIN
    SELECT RAISE(ABORT, 'evolution_decisions is immutable');
END;
