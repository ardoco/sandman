CREATE TABLE samples (
    ts INTEGER NOT NULL,
    series TEXT NOT NULL,
    key TEXT NOT NULL,
    value REAL NOT NULL
);
CREATE INDEX samples_series_key_ts ON samples (series, key, ts);
