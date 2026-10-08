CREATE TABLE IF NOT EXISTS scan_inputs (
    run_id TEXT PRIMARY KEY,
    inputs_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES signal_runs (run_id)
);

CREATE TRIGGER IF NOT EXISTS scan_inputs_no_update
BEFORE UPDATE ON scan_inputs
BEGIN
    SELECT RAISE(ABORT, 'scan_inputs is immutable');
END;

CREATE TRIGGER IF NOT EXISTS scan_inputs_no_delete
BEFORE DELETE ON scan_inputs
BEGIN
    SELECT RAISE(ABORT, 'scan_inputs is immutable');
END;
