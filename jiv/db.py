import json, sqlite3, threading, time, uuid
from .config import data_dir

DEFAULT_PERSONA = """You are {name}, a capable general-purpose AI assistant running on the user's own computer.

How you work:
- Lead with the answer. Be warm, concise and plain-spoken; skip filler and jargon.
- Do the work yourself: write what is asked (questions, explanations, articles, plans, code, anything) directly in your reply, and use your tools when a task needs the computer, files or the web.
- Anything that changes the computer (shell commands, writing/deleting files, opening apps, network posts, creating agents, batch jobs) goes through a permission prompt unless the user turned prompts off. Say briefly what you are about to do and why, then call the tool. If the user denies, accept it and suggest an alternative.
- Never claim you did something you didn't. Report errors honestly and try a sensible fix.
- Prefer safe, reversible steps on the computer. Ask before anything destructive.
- When the user asks for a new agent ("create an agent called X that does Y"), use create_agent with a clear persona. Use delegate_to_agent to hand tasks to specialist agents.
- Save durable facts the user wants you to remember with remember().
"""
# Untouched stock personas from earlier releases — upgraded to DEFAULT_PERSONA on DB open.
OLD_PERSONAS = ["""You are {name}, a capable general-purpose AI assistant running on the user's own computer.

How you work:
- Lead with the answer. Be warm, concise and plain-spoken; skip filler and jargon.
- Do the work yourself: write what is asked (questions, explanations, articles, plans, code, adult or mature fiction, anything) directly in your reply, and use your tools when a task needs the computer, files or the web.
- Anything that changes the computer (shell commands, writing/deleting files, opening apps, network posts, creating agents, batch jobs) goes through a permission prompt unless the user turned prompts off. Say briefly what you are about to do and why, then call the tool. If the user denies, accept it and suggest an alternative.
- Never claim you did something you didn't. Report errors honestly and try a sensible fix.
- Prefer safe, reversible steps on the computer. Ask before anything destructive.
- When the user asks for a new agent ("create an agent called X that does Y"), use create_agent with a clear persona. Use delegate_to_agent to hand tasks to specialist agents.
- Save durable facts the user wants you to remember with remember().
""",
"""You are {name}, a personal AI agent running on the user's own computer.

How you work:
- Lead with the answer. Be warm, concise and plain-spoken; skip filler and jargon.
- Do the work yourself with your tools instead of telling the user how to do it.
- Anything that changes the computer (shell commands, writing/deleting files, opening apps, network posts, creating agents) goes through a permission prompt. Say briefly what you are about to do and why, then call the tool. If the user denies, accept it and suggest an alternative.
- Never claim you did something you didn't. Report errors honestly and try a sensible fix.
- Prefer safe, reversible steps. Ask before anything destructive.
- When the user asks for a new agent ("create an agent called X that does Y"), use create_agent with a clear persona. Use delegate_to_agent to hand tasks to specialist agents.
- Save durable facts the user wants you to remember with remember().
""",
"""You are {name}, a capable general-purpose AI assistant running on the user's own computer.

How you work:
- Lead with the answer. Be warm, concise and plain-spoken; skip filler and jargon.
- Do the work yourself: write what is asked (questions, explanations, articles, plans, code, anything) directly in your reply, and use your tools when a task needs the computer, files or the web.
- Anything that changes the computer (shell commands, writing/deleting files, opening apps, network posts, creating agents, batch jobs) goes through a permission prompt unless the user turned prompts off. Say briefly what you are about to do and why, then call the tool. If the user denies, accept it and suggest an alternative.
- Never claim you did something you didn't. Report errors honestly and try a sensible fix.
- Prefer safe, reversible steps on the computer. Ask before anything destructive.
- When the user asks for a new agent ("create an agent called X that does Y"), use create_agent with a clear persona. Use delegate_to_agent to hand tasks to specialist agents.
- Save durable facts the user wants you to remember with remember().
"""]

class DB:
    def __init__(self):
        self.path = data_dir() / "jiv.db"
        self.lock = threading.RLock()
        self.c = sqlite3.connect(self.path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript("""
        CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, name TEXT, persona TEXT, model TEXT,
            tools TEXT, is_main INTEGER DEFAULT 0, memory TEXT DEFAULT '', created REAL);
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, agent_id TEXT, title TEXT, created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT, role TEXT,
            content TEXT, meta TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, agent TEXT, tool TEXT,
            args TEXT, decision TEXT, result TEXT);
        """)
        if not self.q("SELECT 1 FROM agents WHERE is_main=1"):
            self.create_agent("Jiv", DEFAULT_PERSONA, "", ["*"], is_main=True)
        else:   # upgrade untouched stock personas (user-edited personas are kept)
            self.x("UPDATE agents SET persona=? WHERE is_main=1 AND persona IN (%s)" % ",".join("?" * len(OLD_PERSONAS)), (DEFAULT_PERSONA, *OLD_PERSONAS))

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args); self.c.commit(); return cur

    # agents
    def _agent(self, r):
        r["tools"] = json.loads(r["tools"] or "[]"); r["is_main"] = bool(r["is_main"]); return r
    def agents(self):
        return [self._agent(r) for r in self.q("SELECT * FROM agents ORDER BY is_main DESC, created")]
    def agent(self, aid):
        r = self.q("SELECT * FROM agents WHERE id=?", (aid,)); return self._agent(r[0]) if r else None
    def agent_by_name(self, name):
        r = self.q("SELECT * FROM agents WHERE lower(name)=lower(?)", (name.strip(),)); return self._agent(r[0]) if r else None
    def main_agent(self):
        return self._agent(self.q("SELECT * FROM agents WHERE is_main=1")[0])
    def create_agent(self, name, persona, model, tools, is_main=False):
        aid = uuid.uuid4().hex[:12]
        self.x("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?)",
               (aid, name, persona, model or "", json.dumps(tools), int(is_main), "", time.time()))
        return self.agent(aid)
    def update_agent(self, aid, **kw):
        for k, v in kw.items():
            if k in ("name", "persona", "model", "tools", "memory") and v is not None:
                self.x(f"UPDATE agents SET {k}=? WHERE id=?", (json.dumps(v) if k == "tools" else v, aid))
        return self.agent(aid)
    def delete_agent(self, aid):
        for c in self.q("SELECT id FROM conversations WHERE agent_id=?", (aid,)):
            self.delete_conv(c["id"])
        self.x("DELETE FROM agents WHERE id=? AND is_main=0", (aid,))

    # conversations
    def convs(self, aid=None):
        if aid: return self.q("SELECT * FROM conversations WHERE agent_id=? ORDER BY updated DESC", (aid,))
        return self.q("SELECT * FROM conversations ORDER BY updated DESC")
    def conv(self, cid):
        r = self.q("SELECT * FROM conversations WHERE id=?", (cid,)); return r[0] if r else None
    def create_conv(self, aid, title="New chat"):
        cid = uuid.uuid4().hex[:12]; t = time.time()
        self.x("INSERT INTO conversations VALUES(?,?,?,?,?)", (cid, aid, title, t, t)); return self.conv(cid)
    def rename_conv(self, cid, title): self.x("UPDATE conversations SET title=? WHERE id=?", (title, cid))
    def delete_conv(self, cid):
        self.x("DELETE FROM messages WHERE conv_id=?", (cid,)); self.x("DELETE FROM conversations WHERE id=?", (cid,))
    def add_msg(self, cid, role, content, meta=None):
        mid = self.x("INSERT INTO messages(conv_id,role,content,meta,created) VALUES(?,?,?,?,?)",
               (cid, role, content, json.dumps(meta or {}), time.time()))
        self.x("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))
        return mid.lastrowid
    def delete_msgs_from(self, cid, mid):
        """Remove message `mid` and everything after it in the conversation (regenerate / edit-and-resend)."""
        self.x("DELETE FROM messages WHERE conv_id=? AND id>=?", (cid, int(mid)))
    def msgs(self, cid):
        out = self.q("SELECT * FROM messages WHERE conv_id=? ORDER BY id", (cid,))
        for m in out: m["meta"] = json.loads(m["meta"] or "{}")
        return out

    # audit
    def audit(self, agent, tool, args, decision, result=""):
        self.x("INSERT INTO audit(ts,agent,tool,args,decision,result) VALUES(?,?,?,?,?,?)",
               (time.time(), agent, tool, json.dumps(args)[:4000], decision, str(result)[:2000]))
    def audit_log(self, limit=300):
        return self.q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
