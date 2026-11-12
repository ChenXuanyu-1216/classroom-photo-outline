-- ============================================================
-- lecture-syllabus-generator DDL (与 models.py 等价)
-- SQLite 实现，字段与 MySQL 兼容
-- ============================================================

CREATE TABLE IF NOT EXISTS app_user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider VARCHAR(20) NOT NULL,
    app_id VARCHAR(64) NOT NULL,
    subject VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_app_user_identity UNIQUE(provider, app_id, subject)
);

CREATE TABLE IF NOT EXISTS auth_session (
    token_hash VARCHAR(64) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES app_user(id),
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_auth_session_user_id ON auth_session(user_id);
CREATE INDEX IF NOT EXISTS ix_auth_session_expires_at ON auth_session(expires_at);

CREATE TABLE IF NOT EXISTS web_login_challenge (
    scene_hash VARCHAR(64) PRIMARY KEY,
    proof_hash VARCHAR(64) NOT NULL,
    requester_hash VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'waiting',
    user_id INTEGER REFERENCES app_user(id),
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_web_login_challenge_requester_hash ON web_login_challenge(requester_hash);
CREATE INDEX IF NOT EXISTS ix_web_login_challenge_expires_at ON web_login_challenge(expires_at);
CREATE INDEX IF NOT EXISTS ix_web_login_challenge_created_at ON web_login_challenge(created_at);

CREATE TABLE IF NOT EXISTS task (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER REFERENCES app_user(id),
    title       VARCHAR(255) DEFAULT '未命名任务',
    status      VARCHAR(32)  DEFAULT 'pending',  -- pending/queued/running/success/failed/partial
    page_count  INTEGER      DEFAULT 0,
    error_msg   TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_task_owner_id ON task(owner_id);

CREATE TABLE IF NOT EXISTS page (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id              INTEGER NOT NULL,
    page_no              INTEGER NOT NULL,          -- 逻辑页码
    filename             VARCHAR(512) NOT NULL,
    file_path            VARCHAR(512) NOT NULL,
    confidence           REAL    DEFAULT 0.0,
    low_confidence_flag  BOOLEAN DEFAULT 0,
    failed_flag          BOOLEAN DEFAULT 0,
    recognized_text      TEXT,
    recognized_structured JSON,
    error_code           VARCHAR(64),
    error_retryable      BOOLEAN,
    error_msg            TEXT,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES task(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_point (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER NOT NULL,
    page_id       INTEGER NOT NULL,
    content       TEXT NOT NULL,
    kp_type       VARCHAR(64) DEFAULT 'concept',   -- concept/conclusion/method/example
    source_type   VARCHAR(32) DEFAULT '课堂原话',   -- 课堂原话 / AI推断
    source_pages  VARCHAR(128) NOT NULL,           -- 如 "P1" 或 "P1,P2"
    dedup_key     VARCHAR(256) NOT NULL,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES task(id) ON DELETE CASCADE,
    FOREIGN KEY (page_id) REFERENCES page(id)
);

CREATE TABLE IF NOT EXISTS outline (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    markdown    TEXT NOT NULL,
    version     INTEGER DEFAULT 1,
    recognition_provider VARCHAR(32),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES task(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS outline_node (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    outline_id    INTEGER NOT NULL,
    level         INTEGER NOT NULL,   -- 1=模块, 2=知识点
    title         VARCHAR(512) NOT NULL,
    content       TEXT,
    source_type   VARCHAR(32),
    source_pages  VARCHAR(128),
    parent_id     INTEGER,
    sort_order    INTEGER DEFAULT 0,
    FOREIGN KEY (outline_id) REFERENCES outline(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES outline_node(id)
);

CREATE TABLE IF NOT EXISTS export_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    file_size   INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES task(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_page_task ON page(task_id);
CREATE INDEX IF NOT EXISTS idx_kp_task ON knowledge_point(task_id);
CREATE INDEX IF NOT EXISTS idx_outline_task ON outline(task_id);
CREATE INDEX IF NOT EXISTS idx_node_outline ON outline_node(outline_id);
CREATE INDEX IF NOT EXISTS idx_export_task ON export_log(task_id);

-- V2 engineering support for existing F2-F6 operations; no new product flow.
CREATE TABLE IF NOT EXISTS task_job (
    id              VARCHAR(36) PRIMARY KEY,
    task_id         INTEGER NOT NULL,
    active_task_id  INTEGER UNIQUE,
    operation       VARCHAR(20) NOT NULL,
    page_no         INTEGER,
    status          VARCHAR(20) NOT NULL DEFAULT 'queued',
    result          JSON,
    error           JSON,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at      DATETIME,
    finished_at     DATETIME,
    FOREIGN KEY (task_id) REFERENCES task(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_job_task ON task_job(task_id);
CREATE INDEX IF NOT EXISTS idx_task_job_status ON task_job(status);


CREATE TABLE IF NOT EXISTS evaluation_snapshot (
    id VARCHAR(36) PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES task(id),
    job_id VARCHAR(36) UNIQUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sha256 VARCHAR(64) NOT NULL,
    evidence JSON NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evaluation_snapshot_task_id ON evaluation_snapshot(task_id);

CREATE TABLE IF NOT EXISTS evaluation_review (
    id VARCHAR(36) PRIMARY KEY,
    snapshot_id VARCHAR(36) NOT NULL REFERENCES evaluation_snapshot(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    body JSON NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evaluation_review_snapshot_id ON evaluation_review(snapshot_id);
