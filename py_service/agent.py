import os, json
from typing import Optional
from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.callbacks.base import BaseCallbackHandler
from langgraph.prebuilt import create_react_agent
import db

load_dotenv()


def get_or_create_hub() -> int:
    """Get or create the persistent Agent Hub thread."""
    existing = db.query("SELECT id FROM threads WHERE title = '[hub] Agent Channel' LIMIT 1")
    if existing:
        return existing[0]["id"]
    rows = db.query("INSERT INTO threads (title) VALUES ('[hub] Agent Channel') RETURNING id")
    return rows[0]["id"]


class AgentLogger(BaseCallbackHandler):
    """Logs every thinking step, tool call, and result to agent_logs table."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self._step = 0

    def _log(self, log_type: str, message: str):
        try:
            db.execute(
                "INSERT INTO agent_logs (agent_name, type, message) VALUES (%s, %s, %s)",
                (self.agent_name, log_type, message[:600]),
            )
            # Keep only the last 300 entries per agent
            db.execute(
                """
                DELETE FROM agent_logs WHERE agent_name = %s AND id NOT IN (
                    SELECT id FROM agent_logs WHERE agent_name = %s
                    ORDER BY created_at DESC LIMIT 300
                )
                """,
                (self.agent_name, self.agent_name),
            )
        except Exception:
            pass

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._step += 1
        self._log("thinking", f"Step {self._step} — deciding next action…")

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = serialized.get("name", "unknown")
        try:
            parsed = json.loads(input_str)
            args = ", ".join(f"{k}={repr(v)[:80]}" for k, v in parsed.items())
        except Exception:
            args = str(input_str)[:200]
        self._log("tool_call", f"{name}({args})")

    def on_tool_end(self, output, **kwargs):
        self._log("tool_result", str(output)[:500])

    def on_llm_end(self, response, **kwargs):
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            if msg is None:
                return
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                content = msg.content if isinstance(msg.content, str) else ""
                if content.strip():
                    self._log("response", content[:500])
        except Exception:
            pass


def make_tools(agent_name: str):
    @tool
    def send_message_to_agent(target_agent: str, message: str) -> str:
        """Post a message to another agent in the shared Agent Hub channel."""
        hub_id = get_or_create_hub()
        db.execute(
            "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, %s, 'user', %s)",
            (hub_id, agent_name, f"@{target_agent} {message}"),
        )
        db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (hub_id,))
        return f"Message posted to @{target_agent} in hub."

    @tool
    def create_task(title: str, description: str, assigned_to: str, thread_id: Optional[int] = None) -> str:
        """Create a task and assign it to an agent (coordinator, dev, or admin). Leave thread_id unset if unknown."""
        safe_thread_id = thread_id if thread_id and thread_id > 0 else None
        rows = db.query(
            "INSERT INTO tasks (title, description, assigned_to, created_by, thread_id) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (title, description, assigned_to, agent_name, safe_thread_id),
        )
        return f"Task '{title}' created (id={rows[0]['id']}) and assigned to {assigned_to}."

    @tool
    def dispatch_to_agent(target_agent: str, instruction: str) -> str:
        """
        Wake up another agent and tell it to work on its pending tasks.
        Use this after assigning tasks so the agent actually processes them immediately.
        target_agent: name of the agent to wake (dev or admin)
        instruction: a brief description of why you are waking them (the tool will
                     automatically attach their full pending task list)
        """
        agents = db.query("SELECT * FROM agents WHERE name = %s", (target_agent,))
        if not agents:
            return f"Agent '{target_agent}' not found."
        ag = agents[0]

        # Always enrich with the actual pending task details so the agent
        # never has to guess what it needs to do.
        pending = db.query(
            "SELECT * FROM tasks WHERE assigned_to = %s AND status = 'pending' ORDER BY created_at LIMIT 10",
            (target_agent,),
        )
        if pending:
            task_lines = "\n".join(
                f"  - Task {t['id']}: {t['title']}" + (f" — {t['description']}" if t["description"] else "")
                for t in pending
            )
            full_instruction = (
                f"{instruction}\n\n"
                f"Your pending tasks ({len(pending)}):\n{task_lines}\n\n"
                f"Work through each task, update its status as you go (in_progress → done), "
                f"and report what you did."
            )
        else:
            full_instruction = instruction + "\n\n(No pending tasks found — check if tasks were assigned correctly.)"

        # Post the instruction to the shared hub so everyone can see it
        hub_id = get_or_create_hub()
        db.execute(
            "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, %s, 'user', %s)",
            (hub_id, agent_name, f"@{target_agent} {full_instruction}"),
        )
        # Immediate ACK before the LLM even starts — lets the caller move on
        db.execute(
            "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, %s, 'system', %s)",
            (hub_id, target_agent, "✓ On it — starting work now."),
        )
        db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (hub_id,))
        db.execute("UPDATE agents SET status = 'busy' WHERE name = %s", (target_agent,))
        return f"__DISPATCH__:{target_agent}:{hub_id}:{full_instruction}"

    @tool
    def list_tasks(assigned_to: str = None, status: str = None) -> str:
        """List tasks. Optionally filter by agent name or status (pending/in_progress/done/failed)."""
        conditions, params = [], []
        if assigned_to:
            conditions.append("assigned_to = %s")
            params.append(assigned_to)
        if status:
            conditions.append("status = %s")
            params.append(status)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = db.query(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT 30",
            params or None,
        )
        return json.dumps(rows, default=str)

    @tool
    def update_task(task_id: int, status: str) -> str:
        """Update a task's status. Values: pending, in_progress, done, failed."""
        db.execute(
            "UPDATE tasks SET status = %s, updated_at = NOW() WHERE id = %s",
            (status, task_id),
        )
        return f"Task {task_id} updated to '{status}'."

    @tool
    def list_clients(search: str = "") -> str:
        """List clients. Optionally search by name or company."""
        if search:
            rows = db.query(
                "SELECT * FROM clients WHERE name ILIKE %s OR company ILIKE %s ORDER BY name LIMIT 20",
                (f"%{search}%", f"%{search}%"),
            )
        else:
            rows = db.query("SELECT * FROM clients ORDER BY name LIMIT 20")
        return json.dumps(rows, default=str)

    @tool
    def add_client(name: str, email: str = "", phone: str = "", company: str = "", notes: str = "") -> str:
        """Add a new client record to the database."""
        rows = db.query(
            "INSERT INTO clients (name, email, phone, company, notes) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (name, email, phone, company, notes),
        )
        return f"Client '{name}' added (id={rows[0]['id']})."

    @tool
    def update_client(client_id: int, name: str = None, email: str = None,
                      phone: str = None, company: str = None, notes: str = None) -> str:
        """Update an existing client record. Only provided fields are changed."""
        fields, params = [], []
        for col, val in [("name", name), ("email", email), ("phone", phone),
                         ("company", company), ("notes", notes)]:
            if val is not None:
                fields.append(f"{col} = %s")
                params.append(val)
        if not fields:
            return "No fields to update."
        fields.append("updated_at = NOW()")
        params.append(client_id)
        db.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id = %s", params)
        return f"Client {client_id} updated."

    @tool
    def remember(key: str, value: str) -> str:
        """
        Save something to your persistent memory. Use descriptive keys.
        Examples: 'client_acme_preference', 'project_evolvepro_stack', 'ongoing_github_setup'.
        Existing keys are overwritten. Call this whenever you learn something worth keeping.
        """
        db.execute(
            """
            INSERT INTO agent_memory (agent_name, key, value)
            VALUES (%s, %s, %s)
            ON CONFLICT (agent_name, key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (agent_name, key, value),
        )
        return f"Remembered: {key}"

    @tool
    def recall(key: str) -> str:
        """Look up a specific memory by key."""
        rows = db.query(
            "SELECT value, updated_at FROM agent_memory WHERE agent_name = %s AND key = %s",
            (agent_name, key),
        )
        if not rows:
            return f"No memory found for key '{key}'."
        return f"{key}: {rows[0]['value']} (updated {rows[0]['updated_at']})"

    @tool
    def list_memories() -> str:
        """List all your stored memories."""
        rows = db.query(
            "SELECT key, value, updated_at FROM agent_memory WHERE agent_name = %s ORDER BY updated_at DESC",
            (agent_name,),
        )
        if not rows:
            return "No memories stored yet."
        return "\n".join(f"[{r['key']}] {r['value']}" for r in rows)

    @tool
    def forget(key: str) -> str:
        """Delete a memory by key."""
        db.execute(
            "DELETE FROM agent_memory WHERE agent_name = %s AND key = %s",
            (agent_name, key),
        )
        return f"Forgotten: {key}"

    base = [
        send_message_to_agent,
        list_tasks,
        update_task,
        list_clients,
        add_client,
        update_client,
        remember,
        recall,
        list_memories,
        forget,
    ]
    # Only coordinator can create tasks and dispatch agents
    if agent_name == "coordinator":
        base = [create_task, dispatch_to_agent] + base
    if agent_name == "dev":
        base += _make_github_tools()
    return base


def _make_github_tools() -> list:
    from github import Github, Auth, GithubException
    import base64 as _b64

    def _gh():
        token = os.getenv("GITHUB_TOKEN", "")
        if not token or token == "YOUR_GITHUB_PAT_HERE":
            raise ValueError("GITHUB_TOKEN is not configured in .env")
        return Github(auth=Auth.Token(token))

    @tool
    def github_whoami() -> str:
        """Return the authenticated GitHub username and account info."""
        g = _gh()
        u = g.get_user()
        return json.dumps({"login": u.login, "name": u.name, "public_repos": u.public_repos})

    @tool
    def github_list_repos() -> str:
        """List all repositories for the authenticated GitHub account."""
        g = _gh()
        repos = [
            {"name": r.name, "full_name": r.full_name, "private": r.private,
             "description": r.description, "url": r.html_url}
            for r in g.get_user().get_repos()
        ]
        return json.dumps(repos)

    @tool
    def github_create_repo(name: str, description: str = "", private: bool = False) -> str:
        """Create a new GitHub repository. Returns the repo URL."""
        g = _gh()
        try:
            repo = g.get_user().create_repo(name=name, description=description, private=private, auto_init=True)
            return json.dumps({"url": repo.html_url, "full_name": repo.full_name, "clone_url": repo.clone_url})
        except GithubException as e:
            return f"Error: {e.data.get('message', str(e))}"

    @tool
    def github_get_file(repo_full_name: str, path: str, branch: str = "main") -> str:
        """Get the contents of a file in a repo. repo_full_name format: 'owner/repo'."""
        g = _gh()
        try:
            repo = g.get_repo(repo_full_name)
            f = repo.get_contents(path, ref=branch)
            content = _b64.b64decode(f.content).decode("utf-8", errors="replace")
            return json.dumps({"path": path, "sha": f.sha, "content": content})
        except GithubException as e:
            return f"Error: {e.data.get('message', str(e))}"

    @tool
    def github_create_or_update_file(
        repo_full_name: str, path: str, content: str, commit_message: str, branch: str = "main"
    ) -> str:
        """Create or update a file in a GitHub repo. content is the raw text to write."""
        g = _gh()
        try:
            repo = g.get_repo(repo_full_name)
            encoded = _b64.b64encode(content.encode()).decode()
            try:
                existing = repo.get_contents(path, ref=branch)
                repo.update_file(path, commit_message, content, existing.sha, branch=branch)
                return f"Updated {path} in {repo_full_name}"
            except GithubException:
                repo.create_file(path, commit_message, content, branch=branch)
                return f"Created {path} in {repo_full_name}"
        except GithubException as e:
            return f"Error: {e.data.get('message', str(e))}"

    @tool
    def github_list_issues(repo_full_name: str, state: str = "open") -> str:
        """List issues in a repo. state: open, closed, or all."""
        g = _gh()
        try:
            repo = g.get_repo(repo_full_name)
            issues = [
                {"number": i.number, "title": i.title, "state": i.state,
                 "url": i.html_url, "body": (i.body or "")[:300]}
                for i in repo.get_issues(state=state)
            ]
            return json.dumps(issues)
        except GithubException as e:
            return f"Error: {e.data.get('message', str(e))}"

    @tool
    def github_create_issue(repo_full_name: str, title: str, body: str = "") -> str:
        """Create a new issue in a GitHub repo."""
        g = _gh()
        try:
            repo = g.get_repo(repo_full_name)
            issue = repo.create_issue(title=title, body=body)
            return json.dumps({"number": issue.number, "url": issue.html_url})
        except GithubException as e:
            return f"Error: {e.data.get('message', str(e))}"

    @tool
    def github_close_issue(repo_full_name: str, issue_number: int, comment: str = "") -> str:
        """Close a GitHub issue, optionally leaving a comment."""
        g = _gh()
        try:
            repo = g.get_repo(repo_full_name)
            issue = repo.get_issue(issue_number)
            if comment:
                issue.create_comment(comment)
            issue.edit(state="closed")
            return f"Issue #{issue_number} closed."
        except GithubException as e:
            return f"Error: {e.data.get('message', str(e))}"

    @tool
    def github_create_pull_request(
        repo_full_name: str, title: str, body: str, head: str, base: str = "main"
    ) -> str:
        """Create a pull request. head is the branch with changes, base is the target branch."""
        g = _gh()
        try:
            repo = g.get_repo(repo_full_name)
            pr = repo.create_pull(title=title, body=body, head=head, base=base)
            return json.dumps({"number": pr.number, "url": pr.html_url})
        except GithubException as e:
            return f"Error: {e.data.get('message', str(e))}"

    @tool
    def github_list_branches(repo_full_name: str) -> str:
        """List all branches in a GitHub repo."""
        g = _gh()
        try:
            repo = g.get_repo(repo_full_name)
            branches = [b.name for b in repo.get_branches()]
            return json.dumps(branches)
        except GithubException as e:
            return f"Error: {e.data.get('message', str(e))}"

    return [
        github_whoami,
        github_list_repos,
        github_create_repo,
        github_get_file,
        github_create_or_update_file,
        github_list_issues,
        github_create_issue,
        github_close_issue,
        github_create_pull_request,
        github_list_branches,
    ]


async def run_agent(
    agent_name: str,
    model: str,
    system_prompt: str,
    thread_id: int,
    user_message: Optional[str] = None,
) -> str:
    llm = ChatOllama(
        model=model,
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.3,
    )
    tools = make_tools(agent_name)
    graph = create_react_agent(llm, tools)

    # Inject persistent memory into system prompt
    memories = db.query(
        "SELECT key, value FROM agent_memory WHERE agent_name = %s ORDER BY updated_at DESC",
        (agent_name,),
    )
    if memories:
        memory_block = "## Your persistent memory (facts you have saved across sessions):\n" + \
            "\n".join(f"- [{m['key']}] {m['value']}" for m in memories)
        full_system_prompt = memory_block + "\n\n" + system_prompt
    else:
        full_system_prompt = system_prompt

    # Only user/assistant roles (exclude system ACKs). Limit 30 to keep hub context manageable.
    history = db.query(
        """SELECT role, content FROM messages
           WHERE thread_id = %s AND role IN ('user', 'assistant')
           ORDER BY created_at DESC LIMIT 30""",
        (thread_id,),
    )
    history = list(reversed(history))

    messages = [SystemMessage(content=full_system_prompt)]
    for row in history:
        cls = HumanMessage if row["role"] == "user" else AIMessage
        messages.append(cls(content=row["content"]))

    if user_message:
        messages.append(HumanMessage(content=user_message))

    db.execute(
        "INSERT INTO agent_logs (agent_name, type, message) VALUES (%s, 'start', %s)",
        (agent_name, f"Starting run (thread {thread_id})"),
    )
    result = await graph.ainvoke(
        {"messages": messages},
        config={"callbacks": [AgentLogger(agent_name)]},
    )
    reply = result["messages"][-1].content

    # Check if coordinator used dispatch_to_agent — execute the pending dispatches
    for msg in result["messages"]:
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content.startswith("__DISPATCH__:"):
            await _execute_dispatch(content)

    return reply


async def _execute_dispatch(dispatch_token: str) -> None:
    """Run a dispatched agent in the hub and persist its reply."""
    try:
        # Format: __DISPATCH__:agent:thread_id:instruction (split max 3 times)
        parts = dispatch_token.split(":", 3)
        if len(parts) != 4:
            return
        _, target_agent, thread_id_str, instruction = parts
        thread_id = int(thread_id_str)

        ags = db.query("SELECT * FROM agents WHERE name = %s", (target_agent,))
        if not ags:
            return
        ag = ags[0]

        # Pass instruction as user_message so LangGraph has a HumanMessage trigger.
        # Hub history (last 30 user/assistant messages) provides context.
        reply = await run_agent(
            agent_name=ag["name"],
            model=ag["model"],
            system_prompt=ag["system_prompt"] or "",
            thread_id=thread_id,
            user_message=instruction,
        )

        db.execute(
            "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, %s, 'assistant', %s)",
            (thread_id, target_agent, reply),
        )
        db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (thread_id,))
        db.execute("UPDATE agents SET status = 'idle' WHERE name = %s", (target_agent,))
    except Exception as e:
        db.execute("UPDATE agents SET status = 'error' WHERE name = %s", (target_agent,))


async def work_agent(agent_name: str) -> dict:
    """Trigger an agent to process all its pending tasks. Called by the /work endpoint."""
    agents = db.query("SELECT * FROM agents WHERE name = %s", (agent_name,))
    if not agents:
        return {"error": f"Agent '{agent_name}' not found"}
    ag = agents[0]

    pending = db.query(
        "SELECT * FROM tasks WHERE assigned_to = %s AND status = 'pending' ORDER BY created_at",
        (agent_name,),
    )
    if not pending:
        return {"agent": agent_name, "message": "No pending tasks.", "processed": 0}

    task_list = "\n".join(
        f"- Task {t['id']}: {t['title']}" + (f" — {t['description']}" if t['description'] else "")
        for t in pending
    )
    instruction = (
        f"You have {len(pending)} pending task(s). Work through each one, "
        f"update their status as you go, and report what you did.\n\n{task_list}"
    )

    rows = db.query(
        "INSERT INTO threads (title) VALUES (%s) RETURNING *",
        (f"[work] {agent_name} task run",),
    )
    thread_id = rows[0]["id"]
    db.execute(
        "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, 'system', 'user', %s)",
        (thread_id, instruction),
    )

    db.execute("UPDATE agents SET status = 'busy' WHERE name = %s", (agent_name,))
    try:
        reply = await run_agent(
            agent_name=ag["name"],
            model=ag["model"],
            system_prompt=ag["system_prompt"] or "",
            thread_id=thread_id,
            user_message=instruction,
        )
        db.execute(
            "INSERT INTO messages (thread_id, sender, role, content) VALUES (%s, %s, 'assistant', %s)",
            (thread_id, agent_name, reply),
        )
        db.execute("UPDATE threads SET updated_at = NOW() WHERE id = %s", (thread_id,))
        db.execute("UPDATE agents SET status = 'idle' WHERE name = %s", (agent_name,))
        return {"agent": agent_name, "processed": len(pending), "reply": reply, "thread_id": thread_id}
    except Exception as e:
        db.execute("UPDATE agents SET status = 'error' WHERE name = %s", (agent_name,))
        return {"agent": agent_name, "error": str(e)}
