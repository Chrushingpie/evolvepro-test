from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
from dotenv import load_dotenv
import re, json, hmac, hashlib, asyncio, os
from datetime import datetime, timezone
import db
import agent as agent_runner

load_dotenv()

app = FastAPI(title="EvolvePro Agent Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    db.init_db()
    asyncio.create_task(_autonomous_loop())


# ── Health ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        db.query("SELECT 1")
        db_status = "ok"
    except Exception:
        db_status = "error"
    return {"status": "ok", "db": db_status}


# ── Agents ────────────────────────────────────────────────────────────────

@app.get("/agents")
async def list_agents():
    return db.query("SELECT * FROM agents ORDER BY name")


class AgentCreate(BaseModel):
    name: str
    role: str
    model: str = "gpt-oss:20b"
    system_prompt: Optional[str] = None


@app.post("/agents", status_code=201)
async def create_agent(body: AgentCreate):
    try:
        rows = db.query(
            "INSERT INTO agents (name, role, model, system_prompt) VALUES (%s,%s,%s,%s) RETURNING *",
            (body.name, body.role, body.model, body.system_prompt),
        )
        return rows[0]
    except Exception as e:
        raise HTTPException(400, str(e))


@app.patch("/agents/{agent_id}")
async def update_agent(agent_id: int, body: AgentCreate):
    fields, params = [], []
    for col, val in [("name", body.name), ("role", body.role),
                     ("model", body.model), ("system_prompt", body.system_prompt)]:
        if val is not None:
            fields.append(f"{col} = %s")
            params.append(val)
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append("id = id")  # no-op to avoid empty SET
    params.append(agent_id)
    db.execute(f"UPDATE agents SET {', '.join(fields)} WHERE id = %s", params)
    rows = db.query("SELECT * FROM agents WHERE id = %s", (agent_id,))
    return rows[0] if rows else {}


@app.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: int):
    db.execute("DELETE FROM agents WHERE id = %s", (agent_id,))


# ── Approvals ─────────────────────────────────────────────────

@app.get("/approvals")
async def list_approvals(status: Optional[str] = None):
    if status:
        return db.query(
            "SELECT * FROM pending_approvals WHERE status = %s ORDER BY created_at DESC",
            (status,),
        )
    return db.query("SELECT * FROM pending_approvals ORDER BY created_at DESC")


class RejectBody(BaseModel):
    reason: Optional[str] = None


@app.post("/approvals/{approval_id}/approve")
async def approve_action(approval_id: int):
    db.execute(
        "UPDATE pending_approvals SET status = 'approved', updated_at = NOW() WHERE id = %s",
        (approval_id,),
    )
    rows = db.query("SELECT * FROM pending_approvals WHERE id = %s", (approval_id,))
    return rows[0] if rows else {}


@app.post("/approvals/{approval_id}/reject")
async def reject_action(approval_id: int, body: RejectBody):
    db.execute(
        "UPDATE pending_approvals SET status = 'rejected', reason = %s, updated_at = NOW() WHERE id = %s",
        (body.reason, approval_id),
    )
    rows = db.query("SELECT * FROM pending_approvals WHERE id = %s", (approval_id,))
    return rows[0] if rows else {}


@app.get("/agents/{agent_name}/memory")
async def get_memory(agent_name: str):
    return db.query(
        "SELECT * FROM agent_memory WHERE agent_name = %s ORDER BY updated_at DESC",
        (agent_name,),
    )


@app.delete("/agents/{agent_name}/memory/{key}", status_code=204)
async def delete_memory(agent_name: str, key: str):
    db.execute(
        "DELETE FROM agent_memory WHERE agent_name = %s AND key = %s",
        (agent_name, key),
    )


@app.get("/agents/{agent_name}/logs")
async def get_logs(agent_name: str, limit: int = 60):
    return db.query(
        "SELECT * FROM agent_logs WHERE agent_name = %s ORDER BY created_at DESC LIMIT %s",
        (agent_name, limit),
    )


@app.delete("/agents/{agent_name}/logs", status_code=204)
async def clear_logs(agent_name: str):
    db.execute("DELETE FROM agent_logs WHERE agent_name = %s", (agent_name,))


@app.post("/agents/{agent_name}/work")
async def trigger_work(agent_name: str):
    """Tell an agent to pick up and process all its pending tasks immediately."""
    result = await agent_runner.work_agent(agent_name)
    if "error" in result:
        raise HTTPException(500, result["error"])
    return result


# ── Hub ──────────────────────────────────────────────────────────────────

@app.get("/hub")
async def get_hub():
    """Return the persistent Agent Hub thread, creating it if needed."""
    hub_id = agent_runner.get_or_create_hub()
    rows = db.query("SELECT * FROM threads WHERE id = %s", (hub_id,))
    return rows[0]


# ── Threads ───────────────────────────────────────────────────────────────

@app.get("/threads")
async def list_threads():
    return db.query("""
        SELECT t.*, COUNT(m.id)::int AS message_count
        FROM threads t
        LEFT JOIN messages m ON m.thread_id = t.id
        GROUP BY t.id
        ORDER BY t.updated_at DESC
    """)


class ThreadCreate(BaseModel):
    title: Optional[str] = None


@app.post("/threads", status_code=201)
async def create_thread(body: ThreadCreate):
    rows = db.query(
        "INSERT INTO threads (title) VALUES (%s) RETURNING *",
        (body.title,),
    )
    return rows[0]


@app.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: int):
    db.execute("DELETE FROM threads WHERE id = %s", (thread_id,))


@app.get("/threads/{thread_id}/messages")
async def get_messages(thread_id: int):
    rows = db.query("SELECT 1 FROM threads WHERE id = %s", (thread_id,))
    if not rows:
        raise HTTPException(404, "Thread not found")
    return db.query(
        "SELECT * FROM messages WHERE thread_id = %s ORDER BY created_at",
        (thread_id,),
    )


# ── Chat ──────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    agent_name: str
    content: str


@app.post("/threads/{thread_id}/chat")
async def chat(thread_id: int, body: ChatMessage):
    threads = db.query("SELECT * FROM threads WHERE id = %s", (thread_id,))
    if not threads:
        raise HTTPException(404, "Thread not found")

    agents = db.query("SELECT * FROM agents WHERE name = %s", (body.agent_name,))
    if not agents:
        raise HTTPException(404, f"Agent '{body.agent_name}' not found")
    ag = agents[0]

    db.execute(
        "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'user', 'user', %s)",
        (thread_id, body.content),
    )
    db.execute("UPDATE agents SET status = 'busy' WHERE name = %s", (body.agent_name,))

    try:
        reply = await agent_runner.run_agent(
            agent_name=ag["name"],
            model=ag["model"],
            system_prompt=ag["system_prompt"] or "",
            thread_id=thread_id,
            user_message=body.content,
        )
    except Exception as e:
        db.execute("UPDATE agents SET status = 'error' WHERE name = %s", (body.agent_name,))
        raise HTTPException(500, str(e))

    db.execute(
        "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, %s, 'assistant', %s)",
        (thread_id, body.agent_name, reply),
    )
    db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (thread_id,))
    db.execute("UPDATE agents SET status = 'idle' WHERE name = %s", (body.agent_name,))

    return {"reply": reply, "agent": body.agent_name, "thread_id": thread_id}


# ── Streaming chat ────────────────────────────────────────────

@app.post("/threads/{thread_id}/chat/stream")
async def chat_stream(thread_id: int, body: ChatMessage):
    threads = db.query("SELECT * FROM threads WHERE id = %s", (thread_id,))
    if not threads:
        raise HTTPException(404, "Thread not found")
    agents = db.query("SELECT * FROM agents WHERE name = %s", (body.agent_name,))
    if not agents:
        raise HTTPException(404, f"Agent '{body.agent_name}' not found")
    ag = agents[0]

    db.execute(
        "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'user', 'user', %s)",
        (thread_id, body.content),
    )
    db.execute("UPDATE agents SET status = 'busy' WHERE name = %s", (body.agent_name,))

    async def generate():
        full_reply = []
        try:
            async for event in agent_runner.stream_agent(
                agent_name=ag["name"],
                model=ag["model"],
                system_prompt=ag["system_prompt"] or "",
                thread_id=thread_id,
                user_message=body.content,
            ):
                if event["type"] == "done":
                    complete = event["content"]
                    db.execute(
                        "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, %s, 'assistant', %s)",
                        (thread_id, body.agent_name, complete),
                    )
                    db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (thread_id,))
                    db.execute("UPDATE agents SET status = 'idle' WHERE name = %s", (body.agent_name,))
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            db.execute("UPDATE agents SET status = 'error' WHERE name = %s", (body.agent_name,))
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Room (multi-agent chat) ───────────────────────────────────

class RoomChat(BaseModel):
    content: str = ""
    agents: List[str]
    hidden: bool = False   # if True, saves message as system context, not user visible


@app.post("/threads/{thread_id}/room")
async def room_chat(thread_id: int, body: RoomChat):
    """Send to agents in the hub. @mentions route to specific agents; no mention = message saved only."""
    threads = db.query("SELECT * FROM threads WHERE id = %s", (thread_id,))
    if not threads:
        raise HTTPException(404, "Thread not found")

    # Save user message (unless it's an auto-continue system nudge)
    if body.content:
        role = "system" if body.hidden else "user"
        sender = "system" if body.hidden else "user"
        db.execute(
            "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, %s, %s, %s)",
            (thread_id, sender, role, body.content),
        )

    # Detect @mentions — only run the called agents
    all_agent_names = [a["name"] for a in db.query("SELECT name FROM agents")]
    mentioned = [n for n in all_agent_names if re.search(rf"@{re.escape(n)}\b", body.content, re.IGNORECASE)]
    active_agents = mentioned if mentioned else body.agents

    if not active_agents:
        # No agents to run — message was saved, nothing to respond
        return {"replies": [], "thread_id": thread_id}

    replies = []
    for agent_name in active_agents:
        ags = db.query("SELECT * FROM agents WHERE name = %s", (agent_name,))
        if not ags:
            continue
        ag = ags[0]

        # Inject room context into system prompt
        members_str = ", ".join(body.agents)
        room_prompt = (
            f"[Room context: you are in a live multi-agent room with {members_str}. "
            f"Respond naturally and concisely. Other agents can see your messages.]\n\n"
            + (ag["system_prompt"] or "")
        )

        db.execute("UPDATE agents SET status = 'busy' WHERE name = %s", (agent_name,))
        try:
            # Strip the @mention prefix so the agent sees a clean instruction
            trigger = re.sub(rf"@{re.escape(agent_name)}\s*", "", body.content, flags=re.IGNORECASE).strip()
            if not trigger:
                trigger = "Continue the conversation."
            reply = await agent_runner.run_agent(
                agent_name=ag["name"],
                model=ag["model"],
                system_prompt=room_prompt,
                thread_id=thread_id,
                user_message=trigger,
            )
            db.execute(
                "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, %s, 'assistant', %s)",
                (thread_id, agent_name, reply),
            )
            db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (thread_id,))
            db.execute("UPDATE agents SET status = 'idle' WHERE name = %s", (agent_name,))
            replies.append({"agent": agent_name, "reply": reply})
        except Exception as e:
            db.execute("UPDATE agents SET status = 'error' WHERE name = %s", (agent_name,))
            replies.append({"agent": agent_name, "error": str(e)})

    return {"replies": replies, "thread_id": thread_id}


# ── Tasks ─────────────────────────────────────────────────────────────────

@app.get("/tasks")
async def list_tasks(assigned_to: Optional[str] = None, status: Optional[str] = None):
    conditions, params = [], []
    if assigned_to:
        conditions.append("assigned_to = %s")
        params.append(assigned_to)
    if status:
        conditions.append("status = %s")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return db.query(
        f"SELECT * FROM tasks {where} ORDER BY created_at DESC",
        params or None,
    )


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    assigned_to: Optional[str] = None
    created_by: Optional[str] = "user"
    thread_id: Optional[int] = None


@app.post("/tasks", status_code=201)
async def create_task(body: TaskCreate):
    rows = db.query(
        "INSERT INTO tasks (title, description, assigned_to, created_by, thread_id) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING *",
        (body.title, body.description, body.assigned_to, body.created_by, body.thread_id),
    )
    return rows[0]


class TaskUpdate(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None


@app.patch("/tasks/{task_id}")
async def update_task(task_id: int, body: TaskUpdate):
    fields, params = [], []
    for col, val in [("status", body.status), ("assigned_to", body.assigned_to),
                     ("title", body.title), ("description", body.description)]:
        if val is not None:
            fields.append(f"{col} = %s")
            params.append(val)
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append("updated_at = NOW()")
    params.append(task_id)
    db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = %s", params)
    rows = db.query("SELECT * FROM tasks WHERE id = %s", (task_id,))
    return rows[0] if rows else {}


@app.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: int):
    db.execute("DELETE FROM tasks WHERE id = %s", (task_id,))


# ── Clients ───────────────────────────────────────────────────────────────

@app.get("/clients")
async def list_clients(search: Optional[str] = None):
    if search:
        return db.query(
            "SELECT * FROM clients WHERE name ILIKE %s OR company ILIKE %s ORDER BY name",
            (f"%{search}%", f"%{search}%"),
        )
    return db.query("SELECT * FROM clients ORDER BY name")


class ClientCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None


@app.post("/clients", status_code=201)
async def create_client(body: ClientCreate):
    rows = db.query(
        "INSERT INTO clients (name, email, phone, company, notes) VALUES (%s,%s,%s,%s,%s) RETURNING *",
        (body.name, body.email, body.phone, body.company, body.notes),
    )
    return rows[0]


class ClientUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None


@app.patch("/clients/{client_id}")
async def update_client(client_id: int, body: ClientUpdate):
    fields, params = [], []
    for col, val in [("name", body.name), ("email", body.email), ("phone", body.phone),
                     ("company", body.company), ("notes", body.notes)]:
        if val is not None:
            fields.append(f"{col} = %s")
            params.append(val)
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append("updated_at = NOW()")
    params.append(client_id)
    db.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id = %s", params)
    rows = db.query("SELECT * FROM clients WHERE id = %s", (client_id,))
    return rows[0] if rows else {}


@app.delete("/clients/{client_id}", status_code=204)
async def delete_client(client_id: int):
    db.execute("DELETE FROM clients WHERE id = %s", (client_id,))


# ── Settings ──────────────────────────────────────────────────────────────

@app.get("/settings")
async def get_settings():
    rows = db.query("SELECT key, value, updated_at FROM settings ORDER BY key")
    return {r["key"]: r for r in rows}


class SettingUpdate(BaseModel):
    value: str


@app.patch("/settings/{key}")
async def update_setting(key: str, body: SettingUpdate):
    db.execute(
        """INSERT INTO settings (key, value) VALUES (%s, %s)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
        (key, body.value),
    )
    return {"key": key, "value": body.value}


# ── Autonomous loop ────────────────────────────────────────────────────────

@app.post("/autonomous/run", status_code=202)
async def trigger_autonomous():
    """Manually trigger one autonomous check immediately."""
    asyncio.create_task(_run_autonomous_check())
    return {"status": "triggered"}


async def _autonomous_loop():
    """Background task: wakes up every minute and runs autonomous check when due."""
    while True:
        try:
            await asyncio.sleep(60)
            rows = db.query("SELECT key, value FROM settings")
            cfg = {r["key"]: r["value"] for r in rows}
            if cfg.get("autonomous_enabled") != "true":
                continue
            interval_min = int(cfg.get("autonomous_interval_minutes", "30"))
            last_str = cfg.get("autonomous_last_run", "")
            if last_str:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_str)).total_seconds() / 60
                if elapsed < interval_min:
                    continue
            await _run_autonomous_check()
        except Exception:
            pass


async def _run_autonomous_check():
    """Check pending tasks and scan GitHub for new issues — no webhook needed."""
    hub_id = agent_runner.get_or_create_hub()
    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('autonomous_last_run', %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
        (now_iso,),
    )

    pending = db.query(
        "SELECT assigned_to, COUNT(*)::int AS n FROM tasks WHERE status = 'pending' GROUP BY assigned_to"
    )
    total = sum(p["n"] for p in pending)
    summary = ", ".join(f"{p['assigned_to']}: {p['n']}" for p in pending) if pending else "none"

    db.execute(
        "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'system', 'system', %s)",
        (hub_id, f"⏰ Autonomous check — pending tasks: {summary}"),
    )
    db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (hub_id,))

    # Step 1: coordinator handles any pending tasks
    if total > 0:
        coord = db.query("SELECT * FROM agents WHERE name = 'coordinator'")
        if coord:
            ag = coord[0]
            db.execute("UPDATE agents SET status = 'busy' WHERE name = 'coordinator'")
            try:
                reply = await agent_runner.run_agent(
                    agent_name=ag["name"],
                    model=ag["model"],
                    system_prompt=ag["system_prompt"] or "",
                    thread_id=hub_id,
                    user_message=(
                        f"Autonomous check: {total} pending task(s) found ({summary}). "
                        f"Review, dispatch your team as needed, and be concise."
                    ),
                )
                db.execute(
                    "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'coordinator', 'assistant', %s)",
                    (hub_id, reply),
                )
                db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (hub_id,))
                db.execute("UPDATE agents SET status = 'idle' WHERE name = 'coordinator'")
            except Exception:
                db.execute("UPDATE agents SET status = 'error' WHERE name = 'coordinator'")

    # Step 2: dev scans GitHub for new open issues it hasn't seen before
    dev = db.query("SELECT * FROM agents WHERE name = 'dev'")
    if dev:
        ag = dev[0]
        db.execute("UPDATE agents SET status = 'busy' WHERE name = 'dev'")
        db.execute(
            "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'dev', 'system', %s)",
            (hub_id, "✓ Scanning GitHub for new issues…"),
        )
        db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (hub_id,))
        try:
            reply = await agent_runner.run_agent(
                agent_name=ag["name"],
                model=ag["model"],
                system_prompt=ag["system_prompt"] or "",
                thread_id=hub_id,
                user_message=(
                    "Autonomous GitHub scan: use github_list_repos to get all repos, then "
                    "github_list_issues (state=open) on each. For every open issue, check your memory "
                    "for a key like 'seen_issue_{repo}_{number}' — if it exists, skip it. "
                    "For any NEW issue not in memory: create a task describing what needs to be done, "
                    "handle it using your github tools, then call remember('seen_issue_{repo}_{number}', 'handled') "
                    "so you never process it twice. If there are no new issues, just say so briefly."
                ),
            )
            db.execute(
                "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'dev', 'assistant', %s)",
                (hub_id, reply),
            )
            db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (hub_id,))
            db.execute("UPDATE agents SET status = 'idle' WHERE name = 'dev'")
        except Exception:
            db.execute("UPDATE agents SET status = 'error' WHERE name = 'dev'")


# ── GitHub Webhooks ────────────────────────────────────────────────────────

@app.post("/webhook/github")
async def github_webhook(request: Request):
    """Receive GitHub webhook events and trigger dev agent."""
    body = await request.body()

    secret = os.getenv("WEBHOOK_SECRET", "")
    if secret:
        sig = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(403, "Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event = request.headers.get("X-GitHub-Event", "unknown")
    message = _fmt_github_event(event, payload)

    if message:
        hub_id = agent_runner.get_or_create_hub()
        db.execute(
            "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'github', 'user', %s)",
            (hub_id, f"[GitHub] {message}"),
        )
        db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (hub_id,))
        asyncio.create_task(_handle_webhook_event(message, hub_id))

    return {"ok": True, "event": event}


class RegisterWebhooks(BaseModel):
    url: str
    secret: str = ""


@app.post("/webhook/github/register")
async def register_webhooks(body: RegisterWebhooks):
    """Install the webhook on every repo the authenticated user has admin access to."""
    from github import Github, Auth, GithubException
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(400, "GITHUB_TOKEN not configured in .env")

    g = Github(auth=Auth.Token(token))
    results = []

    for repo in g.get_user().get_repos():
        if repo.permissions and not repo.permissions.admin:
            results.append({"repo": repo.full_name, "status": "skipped", "reason": "no admin access"})
            continue
        try:
            # Skip if our URL is already registered
            hooks = list(repo.get_hooks())
            if any(body.url in (h.config.get("url", "") or "") for h in hooks):
                results.append({"repo": repo.full_name, "status": "already_registered"})
                continue

            config = {"url": body.url, "content_type": "json", "insecure_ssl": "0"}
            if body.secret:
                config["secret"] = body.secret

            repo.create_hook(
                name="web",
                config=config,
                events=["push", "issues", "pull_request", "issue_comment"],
                active=True,
            )
            results.append({"repo": repo.full_name, "status": "registered"})
        except GithubException as e:
            msg = e.data.get("message", str(e)) if hasattr(e, "data") else str(e)
            results.append({"repo": repo.full_name, "status": "error", "reason": msg})

    return {"results": results, "total": len(results)}


def _fmt_github_event(event: str, payload: dict) -> Optional[str]:
    repo = payload.get("repository", {}).get("full_name", "unknown")
    if event == "issues":
        action = payload.get("action", "")
        issue = payload.get("issue", {})
        if action == "opened":
            return f"Issue #{issue.get('number')} opened in {repo}: \"{issue.get('title')}\" — {issue.get('html_url', '')}"
        if action == "closed":
            return f"Issue #{issue.get('number')} closed in {repo}: \"{issue.get('title')}\""
    elif event == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})
        if action in ("opened", "reopened"):
            return f"PR #{pr.get('number')} opened in {repo}: \"{pr.get('title')}\" — {pr.get('html_url', '')}"
        if action == "closed" and pr.get("merged"):
            return f"PR #{pr.get('number')} merged in {repo}: \"{pr.get('title')}\""
    elif event == "push":
        branch = payload.get("ref", "").replace("refs/heads/", "")
        commits = payload.get("commits", [])
        pusher = payload.get("pusher", {}).get("name", "someone")
        if commits:
            return f"{pusher} pushed {len(commits)} commit(s) to {repo}/{branch}"
    elif event == "issue_comment":
        if payload.get("action") == "created":
            issue = payload.get("issue", {})
            comment = payload.get("comment", {})
            user = comment.get("user", {}).get("login", "someone")
            body_text = (comment.get("body") or "")[:120]
            return f"{user} commented on issue #{issue.get('number')} in {repo}: \"{body_text}\""
    return None


async def _handle_webhook_event(message: str, hub_id: int):
    """Run dev agent in response to a GitHub event (fires after webhook returns 200)."""
    dev = db.query("SELECT * FROM agents WHERE name = 'dev'")
    if not dev:
        return
    ag = dev[0]
    db.execute("UPDATE agents SET status = 'busy' WHERE name = 'dev'")
    db.execute(
        "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'dev', 'system', %s)",
        (hub_id, "✓ Received webhook — reviewing…"),
    )
    db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (hub_id,))
    try:
        reply = await agent_runner.run_agent(
            agent_name=ag["name"],
            model=ag["model"],
            system_prompt=ag["system_prompt"] or "",
            thread_id=hub_id,
            user_message=(
                f"GitHub event received: {message}\n\n"
                f"Review this event and take appropriate action if needed. "
                f"If it's an issue you should handle, work on it now using your github_* tools."
            ),
        )
        db.execute(
            "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'dev', 'assistant', %s)",
            (hub_id, reply),
        )
        db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (hub_id,))
        db.execute("UPDATE agents SET status = 'idle' WHERE name = 'dev'")
    except Exception:
        db.execute("UPDATE agents SET status = 'error' WHERE name = 'dev'")
