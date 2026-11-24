PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS feeds (
  feed_id      TEXT PRIMARY KEY,
  source_name  TEXT,
  source_url   TEXT,
  credit       TEXT,
  cadence_sec  INTEGER,
  notes        TEXT
);

-- Content-addressed payload blobs: one row per distinct content, kept forever.
CREATE TABLE IF NOT EXISTS payloads (
  canon_hash TEXT PRIMARY KEY,
  first_seen TEXT NOT NULL,
  payload    TEXT NOT NULL            -- raw JSON, verbatim
);

-- Every fetch observation, changed or not: this is what proves monitoring
-- continuity and lets unchanged stretches establish duration. Errors are
-- observations too - a gap in coverage is data, never a silent zero.
CREATE TABLE IF NOT EXISTS observations (
  id                INTEGER PRIMARY KEY,
  feed_id           TEXT NOT NULL REFERENCES feeds(feed_id),
  fetched_at        TEXT NOT NULL,           -- when the runner saw it (UTC)
  source_updated_at TEXT,                    -- timestamp the source claims, if any
  canon_hash        TEXT REFERENCES payloads(canon_hash),  -- NULL on error
  status            TEXT NOT NULL,           -- changed | unchanged | error
  error             TEXT,
  obs_uid           TEXT,                    -- unique per observation (feed|time|seq)
  volatile          TEXT                     -- JSON overlay: exact original values of
                                             -- the volatile keys at this fetch
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_uid ON observations(obs_uid);
CREATE INDEX IF NOT EXISTS idx_obs_feed_time ON observations(feed_id, fetched_at);

-- Normalised alert history (Europe/Kyiv day boundaries set by the producer,
-- kept verbatim). partial/gap flags preserved; no row = none recorded,
-- partial=1 = incomplete coverage that day.
CREATE TABLE IF NOT EXISTS alert_onsets (
  day       TEXT NOT NULL,
  oblast    TEXT NOT NULL,
  type      TEXT NOT NULL,
  count     INTEGER NOT NULL,
  partial   INTEGER NOT NULL DEFAULT 0,
  gap_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, oblast, type)
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
