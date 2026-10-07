CREATE TABLE inbox (
    event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    project_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    trigger_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX workflows_project ON workflows (project_id, status);

-- next_attempt_at: earliest start of a PENDING step (debounce or retry backoff), epoch seconds
CREATE TABLE steps (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows (id),
    project_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    skill TEXT NOT NULL,
    input_json TEXT NOT NULL,
    action_key TEXT NOT NULL,
    mergeable INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    a2a_task_id TEXT,
    result_json TEXT,
    error_json TEXT,
    started_at TEXT,
    ended_at TEXT,
    next_attempt_at REAL NOT NULL,
    attempt_at REAL,
    next_poll_at REAL
);
CREATE INDEX steps_workflow ON steps (workflow_id);
CREATE INDEX steps_status ON steps (status, project_id);
CREATE INDEX steps_action_key ON steps (action_key);

-- messageId of every A2A attempt; completion events match on causationid
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL REFERENCES steps (id)
);

CREATE TABLE dead_events (
    event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    error TEXT NOT NULL,
    received_at TEXT NOT NULL
);
