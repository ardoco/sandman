CREATE TABLE users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    global_roles TEXT NOT NULL DEFAULT '',            -- comma separated, subset of {admin}
    disabled INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE memberships (
    project TEXT NOT NULL,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'triager', 'maintainer')),
    PRIMARY KEY (project, username)
);
CREATE TABLE tokens (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    name TEXT NOT NULL,
    secret_sha256 TEXT NOT NULL UNIQUE,
    project_id TEXT,
    max_role TEXT CHECK (max_role IN ('viewer', 'triager', 'maintainer')),
    created_at TEXT NOT NULL,
    expires_at TEXT,
    last_used_at TEXT
);
CREATE TABLE sessions (
    id_sha256 TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE last_visits (
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    project TEXT NOT NULL,
    visited_at TEXT NOT NULL,
    PRIMARY KEY (username, project)
);
