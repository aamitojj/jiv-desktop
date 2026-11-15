"""Tool implementations. Each tool declares whether it changes something (needs permission)."""
import asyncio, html, json, os, platform, re, shutil, subprocess, sys, webbrowser
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse, parse_qs
import httpx

IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"

def default_shell():
    """Name of the shell run_shell uses by default on this OS."""
    if IS_WIN: return "powershell"
    if IS_MAC and shutil.which("zsh"): return "zsh"
    return "bash" if shutil.which("bash") else "sh"

def shell_choices():
    return ["auto", "powershell", "cmd"] if IS_WIN else ["auto", "zsh", "bash", "sh"]

def _p(path):
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()

def _clip(s, n=12000):
    s = str(s); return s if len(s) <= n else s[:n] + f"\n…[truncated {len(s)-n} chars]"

async def run_shell(command: str, shell: str = "auto", cwd: str = "", timeout: int = 120):
    if IS_WIN:
        if shell in ("auto", "powershell"):
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
        else:
            args = ["cmd", "/c", command]
    else:
        sh = default_shell() if shell in ("auto", "", None, "powershell", "cmd") else shell
        if not shutil.which(sh): sh = default_shell()
        args = [sh, "-lc", command] if sh in ("zsh", "bash") else ["sh", "-c", command]
    def go():
        kw = {}
        if IS_WIN: kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        r = subprocess.run(args, capture_output=True, text=True, cwd=cwd or None,
                           timeout=timeout, encoding="utf-8", errors="replace", **kw)
        return f"exit code: {r.returncode}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
    try: return _clip(await asyncio.to_thread(go))
    except subprocess.TimeoutExpired: return f"Timed out after {timeout}s"

async def read_file(path: str, offset: int = 0, max_chars: int = 6000):
    """Read a file's text. `path` may be an uploaded attachment's id or file name. PDF/DOCX/XLSX/PPTX are converted to text."""
    from . import uploads as U
    offset, max_chars = max(0, int(offset or 0)), max(200, min(int(max_chars or 6000), 50000))
    m = U.find(path) if not os.path.isabs(os.path.expanduser(str(path))) else None
    if m:
        if m["kind"] == "image": return f"{m['name']} is an image; its content can only be seen by a vision model."
        if m.get("error"): return m["error"]
        t, label = U.text(m), f"{m['name']} (uploaded)"
    else:
        p = _p(path); k = U.kind_of(p.name)
        if k in ("pdf", "docx", "xlsx", "pptx", "binary"):
            t, err = U.extract_text(p, k)
            if err: return err
        else: t = p.read_text("utf-8", errors="replace")
        label = str(p)
    chunk = t[offset:offset + max_chars]
    more = len(t) - offset - len(chunk)
    return f"[{label}: characters {offset:,}-{offset + len(chunk):,} of {len(t):,}]\n{chunk}" + \
           (f"\n[{more:,} more characters - call read_file again with offset {offset + len(chunk)}]" if more > 0 else "\n[end of file]")

async def write_file(path: str, content: str, append: bool = False):
    p = _p(path); p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f: f.write(content)
    return f"Wrote {len(content)} chars to {p}"

async def delete_file(path: str):
    p = _p(path)
    if p.is_dir(): shutil.rmtree(p)
    else: p.unlink()
    return f"Deleted {p}"

async def list_dir(path: str = "~"):
    p = _p(path); out = []
    for e in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))[:500]:
        try: size = "" if e.is_dir() else f"  {e.stat().st_size} B"
        except Exception: size = ""
        out.append(("[DIR] " if e.is_dir() else "      ") + e.name + size)
    return f"{p}\n" + "\n".join(out)

# Web search / fetch / summarise live in jiv.search (1.6.1). Keep parse_ddg as a
# thin wrapper so older tests and imports keep working.
from . import search as SR
from .config import Config as _CfgForSearch

def _search_cfg():
    """Lazy config so tools work in unit tests without a running server."""
    try:
        from .server import cfg as _cfg
        return _cfg
    except Exception:
        try:
            return _CfgForSearch()
        except Exception:
            return None

def _text(h):
    return SR._text_html(h)

def parse_ddg(h, n=8):
    rows = SR.parse_ddg_html(h, n)
    if not rows:
        return "No results."
    return "\n".join(f"{i}. {r['title']}\n   {r['url']}\n   {r.get('snippet') or ''}" for i, r in enumerate(rows, 1))

async def web_fetch(url: str, max_chars: int = 12000):
    cfg = _search_cfg()
    doc = await SR.fetch_url(url, cfg=cfg, max_chars=max(1000, min(int(max_chars or 12000), 50000)))
    if doc.get("error") and not doc.get("text"):
        return f"Fetch failed for {url}: {doc['error']}"
    head = f"HTTP {doc.get('http_status', '?')} {doc.get('url') or url}"
    if doc.get("cached"):
        head += " (cached)"
    if doc.get("truncated"):
        head += " [truncated]"
    title = doc.get("title") or ""
    body = doc.get("text") or ""
    return _clip(f"{head}\nTitle: {title}\n{body}", 16000)

async def web_search(query: str, max_results: int = 8):
    cfg = _search_cfg()
    t0 = __import__("time").time()
    payload = await SR.search(query, max_results=max_results, cfg=cfg)
    payload["elapsed_ms"] = int((__import__("time").time() - t0) * 1000)
    text = SR.format_search_results(payload)
    if payload.get("elapsed_ms") is not None:
        text += f"\nStats: {payload['elapsed_ms']} ms"
    return text

async def web_summarize(query: str = "", urls: str = "", max_pages: int = 4, mode: str = "extractive"):
    """Search (unless urls given), fetch top pages, return a grounded extractive summary with [n] citations.
    mode=extractive works with no AI. Prefer this for research questions so answers stay grounded."""
    cfg = _search_cfg()
    url_list = []
    if urls:
        url_list = [u.strip() for u in re.split(r"[\s,]+", str(urls)) if u.strip().startswith("http")]
    payload = await SR.research_and_summarize(
        query or "", urls=url_list or None, max_pages=max_pages, cfg=cfg, mode=mode or "extractive")
    return SR.format_summary(payload)

async def http_post(url: str, body: str = "", content_type: str = "application/json"):
    async with httpx.AsyncClient(timeout=30, headers=UA) as c:
        r = await c.post(url, content=body.encode(), headers={"Content-Type": content_type})
    return _clip(f"HTTP {r.status_code}\n{r.text}", 8000)

async def open_target(target: str):
    if IS_WIN:
        os.startfile(target)  # apps, files, URLs
    elif IS_MAC:
        is_path = Path(os.path.expanduser(target)).exists()
        args = ["open", os.path.expanduser(target)] if (is_path or re.match(r"^[a-z]+://", target)) else ["open", "-a", target]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0: return f"Could not open {target}: {r.stderr.strip()}"
    elif re.match(r"^[a-z]+://", target):
        webbrowser.open(target)
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if shutil.which(opener) and (Path(target).exists() or "/" in target): subprocess.Popen([opener, target])
        else: subprocess.Popen(target, shell=True)
    return f"Opened {target}"

async def read_clipboard():
    def go():
        if IS_WIN:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, creationflags=0x08000000)
            return r.stdout
        for cmd in (["pbpaste"], ["wl-paste"], ["xclip", "-o", "-selection", "clipboard"], ["xsel", "-b"]):
            if shutil.which(cmd[0]): return subprocess.run(cmd, capture_output=True, text=True).stdout
        try:
            import tkinter; t = tkinter.Tk(); t.withdraw(); v = t.clipboard_get(); t.destroy(); return v
        except Exception as e: return f"Clipboard unavailable: {e}"
    return _clip(await asyncio.to_thread(go) or "(clipboard empty)")

async def system_info():
    if IS_MAC:
        from . import hardware
        hw = await asyncio.to_thread(hardware.detect)
        return json.dumps({"os": hw["os"], "chip": hw["cpu"]["name"], "cores": hw["cpu"].get("cores"),
            "performance_cores": hw["cpu"].get("performance_cores"), "efficiency_cores": hw["cpu"].get("efficiency_cores"),
            "gpu": [{k: g.get(k) for k in ("name", "gpu_cores", "metal_budget_gb")} for g in hw["gpus"]],
            "memory_gb": hw["ram_total_gb"], "memory_free_gb": hw["ram_free_gb"], "disk": hw["disk"],
            "user": os.environ.get("USER"), "home": str(Path.home()), "shell": default_shell(), "python": platform.python_version()}, indent=2)
    du = shutil.disk_usage(Path.home())
    info = {"os": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
            "user": os.environ.get("USERNAME") or os.environ.get("USER"), "home": str(Path.home()), "shell": default_shell(),
            "cwd": os.getcwd(), "cpus": os.cpu_count(),
            "disk_home_free_gb": round(du.free / 1e9, 1), "disk_home_total_gb": round(du.total / 1e9, 1)}
    return json.dumps(info, indent=2)

# name -> (fn, changes_something, description, params schema)
def S(props, req=()):
    return {"type": "object", "properties": props, "required": list(req)}
STR = {"type": "string"}

async def grammar_check(text: str, lang_hints: bool = True):
    """Correct a sentence/paragraph with rule-based English checks (no AI). Optional Hindi hints."""
    from . import english as EN
    r = EN.grammar_check(text or "", lang_hints=bool(lang_hints))
    lines = []
    if r["changed"]:
        lines.append("Corrected: " + r["corrected"])
    else:
        lines.append("No clear issues found. Text looks fine.")
    if r["issues"]:
        lines.append("")
        lines.append("Issues:")
        for i, iss in enumerate(r["issues"][:20], 1):
            bit = f"{i}. [{iss['rule']}] '{iss['span']}': {iss['explanation']}"
            if iss.get("suggestion"):
                bit += f" → {iss['suggestion']}"
            if iss.get("hint_hi"):
                bit += f" ({iss['hint_hi']})"
            lines.append(bit)
    return "\n".join(lines)


TOOLS = {
 "run_shell": (run_shell, True, f"Run a shell command on this {'Windows PC (PowerShell by default, or cmd)' if IS_WIN else 'Mac (zsh by default)' if IS_MAC else 'Linux machine (bash)'}. Returns stdout/stderr.",
    S({"command": STR, "shell": {"type": "string", "enum": shell_choices()}, "cwd": STR}, ["command"])),
 "read_file": (read_file, False, "Read a file as text: a path on disk, or the id / file name of a file the user attached in chat. "
    "Handles txt/code/csv/json/html, PDF, DOCX, XLSX and PPTX. Use offset to page through long files.",
    S({"path": {"type": "string", "description": "file path, or an attachment id/name"}, "offset": {"type": "integer"}, "max_chars": {"type": "integer"}}, ["path"])),
 "write_file": (write_file, True, "Create or overwrite (or append to) a text file.",
    S({"path": STR, "content": STR, "append": {"type": "boolean"}}, ["path", "content"])),
 "delete_file": (delete_file, True, "Delete a file or folder.", S({"path": STR}, ["path"])),
 "list_dir": (list_dir, False, "List a folder's contents.", S({"path": STR}, ["path"])),
 "web_search": (web_search, False, "Search the web (DuckDuckGo, optional SearXNG, Wikipedia). Deduped and ranked; no API key.",
    S({"query": STR, "max_results": {"type": "integer"}}, ["query"])),
 "web_fetch": (web_fetch, False, "Fetch a URL and return readable text (nav/ads stripped; PDFs via pypdf). Cached on disk.",
    S({"url": STR, "max_chars": {"type": "integer"}}, ["url"])),
 "grammar_check": (grammar_check, False, "Check and correct English grammar/spelling in a short text (rule-based, works with no AI). Explains each fix; optional Hindi hints.",
    S({"text": STR, "lang_hints": {"type": "boolean"}}, ["text"])),
 "web_summarize": (web_summarize, False, "Research a question: search, fetch top pages, return a grounded extractive summary with [n] citations and a source list. Works with no AI (mode=extractive). Use this instead of inventing facts.",
    S({"query": STR, "urls": {"type": "string", "description": "optional comma/space-separated URLs to summarise instead of searching"},
       "max_pages": {"type": "integer"}, "mode": {"type": "string", "enum": ["extractive", "auto"]}}, [])),
 "http_post": (http_post, True, "Send an HTTP POST request.", S({"url": STR, "body": STR, "content_type": STR}, ["url"])),
 "open_app": (open_target, True, "Open an application (by name, e.g. 'Safari' or 'notepad'), a file or a URL on the computer.", S({"target": STR}, ["target"])),
 "read_clipboard": (read_clipboard, False, "Read the clipboard text.", S({})),
 "system_info": (system_info, False, "Get OS, CPU, disk and user info.", S({})),
}
# agent-level tools (implemented in agent.py)
META = {
 "create_agent": (True, "Create a new agent with its own name, persona/system prompt, model and allowed tools.",
    S({"name": STR, "persona": {"type": "string", "description": "System prompt describing the agent's role and style"},
       "model": {"type": "string", "description": "Optional model id; empty = default"},
       "tools": {"type": "array", "items": STR, "description": "Allowed tool names, or ['*'] for all"}}, ["name", "persona"])),
 "delegate_to_agent": (False, "Hand a task to another agent by name and get its answer back.",
    S({"agent": STR, "task": STR}, ["agent", "task"])),
 "list_agents": (False, "List existing agents.", S({})),
 "propose_update": (True, "Draft an update to Jiv's OWN code (bug fix or new feature). Give full new contents for each changed file "
    "(paths relative to the app folder, e.g. 'jiv/tools.py'), a short changelog, and optional deletes. Jiv syntax-checks it, runs the tests "
    "in a temporary copy, and shows the user a diff card; nothing changes until the user clicks Apply.",
    S({"title": STR, "changelog": STR, "files": {"type": "array", "items": {"type": "object", "properties": {"path": STR, "content": STR}, "required": ["path", "content"]}},
       "deletes": {"type": "array", "items": STR}, "version": {"type": "string", "description": "optional new version, default = next patch"}}, ["title", "files"])),
 "remember": (False, "Save a durable fact or preference about the user to long-term memory (e.g. 'The user prefers short answers').",
    S({"note": STR, "kind": {"type": "string", "enum": ["fact", "pref"]}}, ["note"])),
 "recall": (False, "Search long-term memory for facts about the user.", S({"query": STR}, ["query"])),
 "forget": (False, "Delete memories that match the text (or a memory id like '#12').", S({"what": STR}, ["what"])),
 "run_code": (True, "Run a program and get its output. Languages: python, javascript, java, c, cpp, go, rust, csharp, php, ruby, powershell. "
    "Give the COMPLETE program. If it fails, fix the error and call run_code again (max 3 attempts).",
    S({"language": STR, "code": STR, "stdin": {"type": "string", "description": "optional input"}, "timeout": {"type": "integer", "description": "seconds, default 30"}}, ["language", "code"])),
 "save_skill": (True, "Save a procedure that worked (optionally with its script) as a reusable skill.",
    S({"name": STR, "description": STR, "steps": STR, "language": STR, "script": STR}, ["name", "description"])),
 "start_batch": (True, "Start a background batch job for big tasks: repeat a prompt N times, or run it on every item of a file (JSON/CSV/JSONL MCQs, TXT/MD, HTML pages, a folder or zip). "
    "Use it instead of doing many items in chat. 'source' is an attachment id or a file/folder path; 'template' can be 'mcq' (rewrite MCQs + verify answer + explanation) or 'adsense' (rewrite web pages), or 'nclex' with 'topic' and 'count' (generate N new NCLEX-style MCQs with rationale + verify). "
    "Use {item}, {i} and {total} in the prompt. The user sees progress on the Batch page.",
    S({"prompt": STR, "count": {"type": "integer", "description": "repeat N times (no source)"}, "source": {"type": "string", "description": "attachment id or path"},
       "template": STR, "verify": {"type": "boolean"}, "start": {"type": "integer", "description": "first item, 1-based"}, "limit": {"type": "integer", "description": "max items"},
       "topic": {"type": "string", "description": "topic for generator templates, e.g. heart failure"}}, [])),
 "run_skill": (True, "Run a saved skill by name (its script runs with run_code; skills without a script return their steps).",
    S({"name": STR, "stdin": STR}, ["name"])),
}

def all_tool_names(): return list(TOOLS) + list(META)

def needs_permission(name):
    if name in TOOLS: return TOOLS[name][1]
    return META[name][0] if name in META else True

# small local models (<= 8B, ~4k context) get fewer, shorter tool definitions: less to read, fewer wrong calls
SMALL_TOOLSET = ["run_code", "read_file", "write_file", "list_dir", "run_shell", "web_search", "web_fetch", "web_summarize", "grammar_check", "open_app",
                 "system_info", "remember", "recall", "forget", "run_skill", "save_skill", "start_batch"]

def schemas(allowed, small=False):
    if small:
        out = []
        for s_ in schemas(allowed):
            f = s_["function"]
            if f["name"] in SMALL_TOOLSET:
                d = f["description"].split(". ")[0][:140]
                props = {k: {kk: vv for kk, vv in v.items() if kk != "description"} for k, v in f["parameters"].get("properties", {}).items()}
                out.append({"type": "function", "function": {"name": f["name"], "description": d, "parameters": {**f["parameters"], "properties": props}}})
        return out
    return _schemas(allowed)

def _schemas(allowed):
    out = []
    def ok(n): return "*" in allowed or n in allowed
    for n, (fn, ch, d, p) in TOOLS.items():
        if ok(n): out.append({"type": "function", "function": {"name": n, "description": d, "parameters": p}})
    for n, (ch, d, p) in META.items():
        if ok(n) or n in ("list_agents", "remember", "recall", "forget"):
            out.append({"type": "function", "function": {"name": n, "description": d, "parameters": p}})
    return out
