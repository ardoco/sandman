CREATE TABLE suggestions (
    id TEXT PRIMARY KEY,
    inconsistency_id TEXT NOT NULL,
    project TEXT NOT NULL,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    route TEXT NOT NULL,
    resolved_model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    payload TEXT NOT NULL                              -- JSON: suggestions, questions, warnings
);
CREATE INDEX suggestions_inconsistency ON suggestions (inconsistency_id, created_at);
