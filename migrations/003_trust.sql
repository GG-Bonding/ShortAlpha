ALTER TABLE signal_runs ADD COLUMN experiment_id TEXT;

ALTER TABLE strategy_registry ADD COLUMN rules_json TEXT;

ALTER TABLE experiments ADD COLUMN shadow_starts_at TEXT;
ALTER TABLE experiments ADD COLUMN training_cutoff_at TEXT;
ALTER TABLE experiments ADD COLUMN baseline_rules_json TEXT;
ALTER TABLE experiments ADD COLUMN promotion_json TEXT;
