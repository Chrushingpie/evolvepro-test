import os
from dotenv import load_dotenv
import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor

load_dotenv()

_pool: pg_pool.SimpleConnectionPool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100) UNIQUE NOT NULL,
    role          VARCHAR(100) NOT NULL,
    model         VARCHAR(100) NOT NULL DEFAULT 'gpt-oss:20b',
    status        VARCHAR(20)  NOT NULL DEFAULT 'idle',
    system_prompt TEXT,
    created_at    TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS threads (
    id          SERIAL PRIMARY KEY,
    title       VARCHAR(255),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    thread_id   INTEGER REFERENCES threads(id) ON DELETE CASCADE,
    sender      VARCHAR(100) NOT NULL,
    role        VARCHAR(20)  NOT NULL,
    content     TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tasks (
    id          SERIAL PRIMARY KEY,
    title       VARCHAR(255) NOT NULL,
    description TEXT,
    status      VARCHAR(20)  NOT NULL DEFAULT 'pending',
    assigned_to VARCHAR(100),
    created_by  VARCHAR(100),
    thread_id   INTEGER REFERENCES threads(id),
    created_at  TIMESTAMPTZ  DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS clients (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    email       VARCHAR(255),
    phone       VARCHAR(50),
    company     VARCHAR(255),
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id          SERIAL PRIMARY KEY,
    agent_name  VARCHAR(100),
    tool_name   VARCHAR(100),
    input       JSONB,
    output      JSONB,
    duration_ms INTEGER,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_memory (
    id          SERIAL PRIMARY KEY,
    agent_name  VARCHAR(100) NOT NULL,
    key         VARCHAR(255) NOT NULL,
    value       TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (agent_name, key)
);

CREATE TABLE IF NOT EXISTS agent_logs (
    id          SERIAL PRIMARY KEY,
    agent_name  VARCHAR(100) NOT NULL,
    type        VARCHAR(20)  NOT NULL,
    message     TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_time
    ON agent_logs (agent_name, created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT         NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ  DEFAULT NOW()
);
"""

DEFAULT_AGENTS = [
    {
        "name": "coordinator",
        "role": "Coordinator",
        "model": "gpt-oss:20b",
        "system_prompt": (
            "You are the Coordinator agent for EvolvePro, a local multi-agent automation system. "
            "Your job is to understand requests, break them into tasks, and delegate to the right agents. "
            "You have two specialist agents: 'dev' (handles code, GitHub, and technical tasks) and "
            "'admin' (handles email, client management, and scheduling). "
            "When delegating: (1) create a task with create_task including a DETAILED description of exactly "
            "what needs to be done — repo names, file paths, specific actions. (2) immediately call "
            "dispatch_to_agent — the dispatch tool will automatically attach the full task list to your message "
            "so you only need a one-line summary in the instruction param. "
            "Never dispatch with vague instructions like 'process your tasks' — the task description must be "
            "specific enough that the agent can act without asking clarifying questions. "
            "Use remember() to save project decisions and context. "
            "Be concise and action-oriented."
        ),
    },
    {
        "name": "dev",
        "role": "Developer",
        "model": "gpt-oss:20b",
        "system_prompt": (
            "You are the Dev agent for EvolvePro. You specialise in software development and have real tools "
            "to interact with GitHub: create repos, read and write files, open and close issues, create pull requests. "
            "When asked to do something on GitHub, use your github_* tools to actually do it — do not just describe it. "
            "Always call github_whoami first if you are unsure of the authenticated user. "
            "Use remember() to save important project context: repo names, tech stack decisions, client project mappings. "
            "Use list_memories() at the start of tasks to recall what you already know about a project. "
            "When assigned a task, complete it using your tools, update the task status, and report what you did."
        ),
    },
    {
        "name": "admin",
        "role": "Administrator",
        "model": "gpt-oss:20b",
        "system_prompt": (
            "You are the Admin agent for EvolvePro. You specialise in administrative work: "
            "managing client records, drafting and responding to emails, scheduling, and record keeping. "
            "Use remember() to save important context: client preferences, ongoing conversations, follow-up dates, "
            "communication history. Use list_memories() before handling client tasks so you have full context. "
            "When assigned a task, complete it thoroughly and report back to the coordinator."
        ),
    },
]


def get_pool() -> pg_pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = pg_pool.SimpleConnectionPool(
            1, 10,
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            dbname=os.getenv("DB_NAME", "evolvepro"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD"),
        )
    return _pool


def query(sql: str, params=None) -> list[dict]:
    conn = get_pool().getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            result = cur.fetchall()
        conn.commit()
        return [dict(r) for r in result]
    except Exception:
        conn.rollback()
        raise
    finally:
        get_pool().putconn(conn)


def execute(sql: str, params=None) -> None:
    conn = get_pool().getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        get_pool().putconn(conn)


def init_db() -> None:
    conn = get_pool().getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
    finally:
        get_pool().putconn(conn)

    for row in [
        ("autonomous_enabled", "false"),
        ("autonomous_interval_minutes", "30"),
    ]:
        execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            row,
        )

    for ag in DEFAULT_AGENTS:
        execute(
            """
            INSERT INTO agents (name, role, model, system_prompt)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            (ag["name"], ag["role"], ag["model"], ag["system_prompt"]),
        )
