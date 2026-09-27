CREATE TABLE IF NOT EXISTS market_data (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    bar_type TEXT NOT NULL CHECK (bar_type IN ('daily', 'premarket')),
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    event_time TEXT NOT NULL,
    published_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, session_date, bar_type)
);

CREATE TABLE IF NOT EXISTS news_events (
    id TEXT PRIMARY KEY,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    source TEXT NOT NULL,
    event_time TEXT NOT NULL,
    published_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_event_symbols (
    news_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    PRIMARY KEY (news_id, symbol),
    FOREIGN KEY (news_id) REFERENCES news_events (id)
);

CREATE TABLE IF NOT EXISTS signal_runs (
    run_id TEXT PRIMARY KEY,
    signal_date TEXT NOT NULL,
    signal_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    code_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    universe_size INTEGER,
    eligible_size INTEGER,
    scored_size INTEGER,
    candidate_size INTEGER,
    duration_ms INTEGER,
    provider_errors INTEGER NOT NULL,
    missing_data_count INTEGER NOT NULL,
    market_regime TEXT,
    no_trade INTEGER NOT NULL,
    status TEXT NOT NULL,
    universe_list_as_of TEXT,
    point_in_time_membership INTEGER NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_snapshots (
    run_id TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES signal_runs (run_id)
);

CREATE TABLE IF NOT EXISTS factor_values (
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    factor TEXT NOT NULL,
    raw_value REAL,
    normalized_value REAL,
    score REAL,
    available INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    details_json TEXT NOT NULL,
    PRIMARY KEY (run_id, symbol, factor),
    FOREIGN KEY (run_id) REFERENCES signal_runs (run_id)
);

CREATE TABLE IF NOT EXISTS forward_returns (
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    horizon INTEGER NOT NULL CHECK (horizon IN (1, 2, 3, 5)),
    entry_session_date TEXT,
    entry_price REAL,
    exit_session_date TEXT,
    exit_price REAL,
    stock_return REAL,
    spy_return REAL,
    excess_return REAL,
    mae REAL,
    mfe REAL,
    code_version TEXT NOT NULL,
    PRIMARY KEY (run_id, symbol, horizon),
    FOREIGN KEY (run_id) REFERENCES signal_runs (run_id)
);

CREATE INDEX IF NOT EXISTS idx_market_symbol_date ON market_data (symbol, session_date);
CREATE INDEX IF NOT EXISTS idx_news_available ON news_events (available_at);
CREATE INDEX IF NOT EXISTS idx_runs_date ON signal_runs (signal_date);

CREATE TRIGGER IF NOT EXISTS market_data_no_update
BEFORE UPDATE ON market_data
BEGIN
    SELECT RAISE(ABORT, 'market_data is immutable');
END;

CREATE TRIGGER IF NOT EXISTS market_data_no_delete
BEFORE DELETE ON market_data
BEGIN
    SELECT RAISE(ABORT, 'market_data is immutable');
END;

CREATE TRIGGER IF NOT EXISTS news_events_no_update
BEFORE UPDATE ON news_events
BEGIN
    SELECT RAISE(ABORT, 'news_events is immutable');
END;

CREATE TRIGGER IF NOT EXISTS news_events_no_delete
BEFORE DELETE ON news_events
BEGIN
    SELECT RAISE(ABORT, 'news_events is immutable');
END;

CREATE TRIGGER IF NOT EXISTS news_event_symbols_no_update
BEFORE UPDATE ON news_event_symbols
BEGIN
    SELECT RAISE(ABORT, 'news_event_symbols is immutable');
END;

CREATE TRIGGER IF NOT EXISTS news_event_symbols_no_delete
BEFORE DELETE ON news_event_symbols
BEGIN
    SELECT RAISE(ABORT, 'news_event_symbols is immutable');
END;

CREATE TRIGGER IF NOT EXISTS signal_runs_no_update
BEFORE UPDATE ON signal_runs
BEGIN
    SELECT RAISE(ABORT, 'signal_runs is immutable');
END;

CREATE TRIGGER IF NOT EXISTS signal_runs_no_delete
BEFORE DELETE ON signal_runs
BEGIN
    SELECT RAISE(ABORT, 'signal_runs is immutable');
END;

CREATE TRIGGER IF NOT EXISTS signal_snapshots_no_update
BEFORE UPDATE ON signal_snapshots
BEGIN
    SELECT RAISE(ABORT, 'signal_snapshots are immutable');
END;

CREATE TRIGGER IF NOT EXISTS signal_snapshots_no_delete
BEFORE DELETE ON signal_snapshots
BEGIN
    SELECT RAISE(ABORT, 'signal_snapshots are immutable');
END;

CREATE TRIGGER IF NOT EXISTS factor_values_no_update
BEFORE UPDATE ON factor_values
BEGIN
    SELECT RAISE(ABORT, 'factor_values is immutable');
END;

CREATE TRIGGER IF NOT EXISTS factor_values_no_delete
BEFORE DELETE ON factor_values
BEGIN
    SELECT RAISE(ABORT, 'factor_values is immutable');
END;

CREATE TRIGGER IF NOT EXISTS forward_returns_no_update
BEFORE UPDATE ON forward_returns
BEGIN
    SELECT RAISE(ABORT, 'forward_returns is immutable');
END;

CREATE TRIGGER IF NOT EXISTS forward_returns_no_delete
BEFORE DELETE ON forward_returns
BEGIN
    SELECT RAISE(ABORT, 'forward_returns is immutable');
END;
