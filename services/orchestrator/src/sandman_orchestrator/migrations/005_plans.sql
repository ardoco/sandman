-- LLM planner (T6.4): one row per plan; its workflow holds the BLOCKED steps until the plan is approved.
-- expires_at: epoch seconds; status: AWAITING, APPROVED, REJECTED, EXPIRED
CREATE TABLE plans (
    workflow_id TEXT PRIMARY KEY REFERENCES workflows (id),
    project_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    status TEXT NOT NULL,
    model TEXT NOT NULL,
    resolved_model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    reason TEXT
);
