
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
 run_id TEXT PRIMARY KEY, snapshot_day TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, status TEXT NOT NULL, expected_count INTEGER, observed_count INTEGER,
 error TEXT, raw_dir TEXT NOT NULL, config_json TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_success_per_day ON runs(snapshot_day) WHERE status='success';
CREATE TABLE IF NOT EXISTS vehicles(
 vehicle_id TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 first_missing_at TEXT, missing_successful_days INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL, last_business_json TEXT NOT NULL, last_business_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations(
 run_id TEXT NOT NULL REFERENCES runs(run_id), vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id),
 observed_at TEXT NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(run_id,vehicle_id));
CREATE INDEX IF NOT EXISTS observations_vehicle ON observations(vehicle_id,observed_at);
CREATE TABLE IF NOT EXISTS events(
 event_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id), detected_at TEXT NOT NULL,
 kind TEXT NOT NULL, field TEXT, old_json TEXT, new_json TEXT);
CREATE INDEX IF NOT EXISTS events_vehicle ON events(vehicle_id,detected_at);
