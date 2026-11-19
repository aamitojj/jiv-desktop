import asyncio, json, uuid
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from .config import Config, PROVIDERS, SESSION_TOKEN, data_dir
from .db import DB
from .agent import Permissions, Runner
from . import providers as P, tools as T
from .providers import ProviderError
from . import hardware as HW, models_catalog as MC, ollama_mgr as OM

STATIC = Path(__file__).parent / "static"
cfg = Config(); db = DB(); perms = Permissions(cfg, db); runner = Runner(cfg, db, perms)
from .learning import Learning, auto_extract
from . import languages as LANG
learn = Learning(db); runner.learn = learn
from . import batch as BT
from . import builder as BD
batch = BT.BatchManager(db, cfg); runner.batch = batch
from . import agent as _AG; _AG.BATCH_MGR["m"] = batch
RUNS = {}
RUN_CONV = {}   # run id -> {"conv": conversation id}
app = FastAPI(title="Jiv Desktop", docs_url=None, redoc_url=None, openapi_url=None)

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in ALLOWED_HOSTS:                      # DNS-rebinding protection
        return JSONResponse({"error": "bad host"}, 403)
    origin = request.headers.get("origin")
    if origin and origin != "null":
        oh = origin.split("://", 1)[-1].rsplit(":", 1)[0]
        if oh not in ALLOWED_HOSTS: return JSONResponse({"error": "bad origin"}, 403)
    tok = request.headers.get("x-jiv-token")
    if not tok and request.method == "GET" and request.url.path.startswith("/api/uploads/") and request.url.path.endswith("/raw"):
        tok = request.query_params.get("t")  # <img> previews can't send headers
    if not tok and request.method == "GET" and request.url.path.startswith("/api/batch/jobs/") and "/download/" in request.url.path:
        tok = request.query_params.get("t")  # download links
    if not tok and request.method == "GET" and request.url.path.startswith("/api/builder/projects/") and "/download/" in request.url.path:
        tok = request.query_params.get("t")
    if request.url.path.startswith("/api/") and tok != SESSION_TOKEN:
        return JSONResponse({"error": "missing or invalid session token"}, 403)
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
    return resp

@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC / "index.html").read_text("utf-8").replace("__JIV_TOKEN__", SESSION_TOKEN)

app.mount("/static", StaticFiles(directory=STATIC), name="static")

def state():
    from . import __version__ as _v
    return {"version": _v, "config": cfg.public(), "providers": PROVIDERS, "agents": db.agents(), "conversations": db.convs(),
            "tools": [{"name": n, "changes": T.needs_permission(n)} for n in T.all_tool_names()],
            "data_dir": str(data_dir())}

@app.get("/api/state")
async def get_state(): return state()

@app.post("/api/settings")
async def settings(req: Request):
    d = await req.json()
    if str(d.get("api_key", "")).startswith("••••"): d.pop("api_key")
    if "temperature" in d: d["temperature"] = max(0.0, min(2.0, float(d["temperature"])))
    if "model" in d and not str(d.get("model") or "").strip(): d.pop("model")   # never wipe the model with an empty pick
    if d.get("think_mode") not in (None, "off", "auto", "on"): d.pop("think_mode")
    if "fallback_models" in d:
        fm = d["fallback_models"]; fm = fm.split(",") if isinstance(fm, str) else fm
        d["fallback_models"] = [str(x).strip() for x in (fm or []) if str(x).strip() and not any(ch in str(x) for ch in " \"\n;&|")][:5]
    if "stall_timeout" in d: d["stall_timeout"] = max(20, min(900, int(float(d["stall_timeout"] or 180))))
    if "searx_url" in d:
        su = str(d.get("searx_url") or "").strip()
        if su and not (su.startswith("http://") or su.startswith("https://")): d["searx_url"] = ""
        else: d["searx_url"] = su.rstrip("/")
    if "search_cache_ttl" in d: d["search_cache_ttl"] = max(0, min(86400, int(float(d["search_cache_ttl"] or 3600))))
    cfg.update(d); return cfg.public()

@app.get("/api/fallback")
async def fallback_status():
    """Which INSTALLED model would answer if the selected one fails technically (read-only; never downloads)."""
    from . import reliability as REL
    from urllib.parse import urlparse
    base, ourl, model = cfg.get("base_url") or "", OURL(), cfg.get("model") or ""
    out = {"enabled": bool(cfg.get("fallback_enabled", True)), "preferred": cfg.get("fallback_models") or [], "primary": model,
           "same_server": urlparse(base).netloc == urlparse(ourl).netloc, "installed": [], "effective": None}
    try: out["installed"] = await REL.installed_models(ourl)
    except Exception as e: out["error"] = str(e)[:200]; return out
    if out["same_server"]:
        out["effective"] = REL.choose_fallback(out["installed"], model, out["preferred"], float(cfg.get("fallback_max_gb", 3.5) or 3.5))
    return out

@app.get("/api/connection")
async def connection():
    """Is the model server reachable? Used by the reconnect banner."""
    base = cfg.get("base_url") or ""; model = cfg.get("model") or ""
    out = {"base_url": base, "model": model, "is_ollama": "11434" in base or cfg.get("provider") == "ollama"}
    if not base: return {**out, "ok": False, "error": "No model server configured."}
    try:
        ms = await P.list_models(base, cfg.get("api_key", ""), timeout=3.0)
        return {**out, "ok": True, "model_listed": (not model) or model in ms or any(m.split(":")[0] == model.split(":")[0] and model.endswith(":latest") for m in ms), "count": len(ms)}
    except Exception as e:
        return {**out, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}

async def _conn(d):
    key = d.get("api_key") or ""
    if not key or key.startswith("••••"): key = cfg.get("api_key", "")
    return (d.get("base_url") or cfg.get("base_url")), key

@app.post("/api/models")
async def models(req: Request):
    base, key = await _conn(await req.json())
    try: return {"ok": True, "models": await P.list_models(base, key)}
    except Exception as e: return {"ok": False, "error": str(e)[:300], "models": []}

@app.post("/api/test")
async def test(req: Request):
    d = await req.json(); base, key = await _conn(d); model = d.get("model") or cfg.get("model")
    try:
        out = ""
        async for k, v in P.stream_chat(base, key, model, [{"role": "user", "content": "Reply with just: OK"}], 0):
            if k == "text": out += v
        return {"ok": True, "reply": out[:200]}
    except Exception as e: return {"ok": False, "error": str(e)[:400]}

@app.get("/api/autodetect")
async def autodetect(): return {"found": await P.autodetect()}

# agents
@app.post("/api/agents")
async def new_agent(req: Request):
    d = await req.json()
    if not d.get("name"): raise HTTPException(400, "name required")
    if db.agent_by_name(d["name"]): raise HTTPException(400, "name exists")
    return db.create_agent(d["name"], d.get("persona") or "You are {name}, a helpful assistant.", d.get("model", ""), d.get("tools") or ["*"])

@app.put("/api/agents/{aid}")
async def edit_agent(aid: str, req: Request):
    d = await req.json(); return db.update_agent(aid, **{k: d.get(k) for k in ("name", "persona", "model", "tools", "memory")})

@app.delete("/api/agents/{aid}")
async def del_agent(aid: str):
    a = db.agent(aid)
    if a and a["is_main"]: raise HTTPException(400, "The main agent can be renamed but not deleted.")
    db.delete_agent(aid); return {"ok": True}

# conversations
@app.get("/api/conversations/{cid}")
async def get_conv(cid: str): return {"conversation": db.conv(cid), "messages": db.msgs(cid), "ratings": learn.ratings(cid)}

# ---------- learning: memory, skills, feedback ----------
@app.get("/api/memory")
async def memory_list(): return {"memories": learn.memories(), "auto_memory": cfg.get("auto_memory", True)}
@app.post("/api/memory")
async def memory_add(req: Request):
    d = await req.json(); mid = learn.add(d.get("text", ""), d.get("kind", "fact"), "user")
    if not mid: raise HTTPException(400, "empty memory")
    return {"id": mid}
@app.post("/api/memory/merge")
async def memory_merge(): return {"merged": learn.merge_duplicates()}
@app.put("/api/memory/{mid}")
async def memory_edit(mid: int, req: Request):
    d = await req.json(); learn.update(mid, d.get("text"), d.get("kind")); return {"ok": True}
@app.delete("/api/memory/{mid}")
async def memory_del(mid: int): learn.delete(mid); return {"ok": True}
@app.post("/api/memory/settings")
async def memory_settings(req: Request):
    d = await req.json(); cfg.update({"auto_memory": bool(d.get("auto_memory"))}); return {"ok": True}

@app.get("/api/skills")
async def skills_list(): return {"skills": learn.skills()}
@app.post("/api/skills")
async def skills_save(req: Request):
    d = await req.json()
    try: sk = learn.save_skill(d.get("name"), d.get("description", ""), d.get("steps", ""), d.get("language", ""), d.get("script", ""))
    except ValueError as e: raise HTTPException(400, str(e))
    db.audit("user", "save_skill", {"name": sk["name"]}, "allowed"); return sk
@app.put("/api/skills/{sid}")
async def skills_edit(sid: int, req: Request):
    learn.update_skill(sid, **(await req.json())); return {"ok": True}
@app.delete("/api/skills/{sid}")
async def skills_del(sid: int): learn.delete_skill(sid); return {"ok": True}

@app.post("/api/feedback")
async def feedback(req: Request):
    d = await req.json(); cid, mid = d.get("conv_id"), int(d.get("msg_id") or 0)
    msgs = db.msgs(cid or ""); ans = next((m for m in msgs if m["id"] == mid), None)
    if not ans: raise HTTPException(404, "message not found")
    question = next((m["content"] for m in reversed(msgs) if m["id"] < mid and m["role"] == "user"), "")
    return learn.feedback(cid, mid, d.get("rating", 1), d.get("correction", ""), question, ans["content"])

# ---------- languages ----------
@app.get("/api/languages")
async def languages(force: int = 0):
    return {"runtimes": await asyncio.to_thread(LANG.detect, bool(force)), "workspace": str(LANG.workspace()),
            "installer": "winget" if (LANG.IS_WIN or os.environ.get("JIV_WINGET")) else "brew" if LANG.IS_MAC else "",
            "installer_found": bool(LANG.winget_exe()) if (LANG.IS_WIN or os.environ.get("JIV_WINGET")) else bool(__import__("shutil").which("brew")) if LANG.IS_MAC else False}
@app.post("/api/languages/install")
async def languages_install(req: Request):
    d = await req.json(); lid = d.get("id")
    if lid not in LANG.RUNTIMES: raise HTTPException(400, "unknown language")
    if d.get("approved") is not True:
        db.audit("user", "install_language", {"id": lid}, "denied"); raise HTTPException(400, "This needs your permission first.")
    try: jid = await LANG.install(lid)
    except ValueError as e: raise HTTPException(400, str(e))
    db.audit("user", "install_language", {"id": lid, "cmd": LANG.install_cmd(lid)}, "allowed")
    return {"job": jid}
@app.get("/api/languages/jobs/{jid}")
async def languages_job(jid: str):
    j = LANG.job_view(jid)
    if not j: raise HTTPException(404, "no such job")
    return j

@app.put("/api/conversations/{cid}")
async def ren_conv(cid: str, req: Request): db.rename_conv(cid, (await req.json())["title"]); return {"ok": True}

@app.delete("/api/conversations/{cid}")
async def del_conv(cid: str): db.delete_conv(cid); return {"ok": True}

@app.get("/api/audit")
async def audit(): return db.audit_log()

@app.post("/api/permission/{pid}")
async def permission(pid: str, req: Request):
    d = (await req.json()).get("decision")
    if d not in ("once", "always", "deny"): raise HTTPException(400, "bad decision")
    return {"ok": perms.resolve(pid, d)}

@app.post("/api/stop/{rid}")
async def stop(rid: str):
    t = RUNS.get(rid)
    if t: t.cancel()
    return {"ok": bool(t)}

# ---------- chat attachments ----------
from . import uploads as UPL

@app.post("/api/uploads")
async def upload_file(req: Request):
    body = await req.body()
    if not body: raise HTTPException(400, "empty file")
    if len(body) > UPL.MAX_UPLOAD: raise HTTPException(413, "File too large (max 50 MB).")
    from urllib.parse import unquote as _uq
    try: meta = await asyncio.to_thread(UPL.save, _uq(req.headers.get("X-Filename") or "file"), body)
    except ValueError as e: raise HTTPException(400, str(e))
    _base, _key, model = runner.conn(db.main_agent())
    meta["vision"] = UPL.is_vision_model(model)
    return meta

@app.get("/api/uploads/{uid}/raw")
async def upload_raw(uid: str):
    from fastapi.responses import FileResponse
    m = UPL.get(uid)
    if not m: raise HTTPException(404, "not found")
    if m["kind"] == "image":  # previews only; other files are never rendered by the browser
        return FileResponse(UPL.file_path(m), media_type=m["mime"], headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})
    return FileResponse(UPL.file_path(m), media_type="application/octet-stream", filename=m["name"], headers={"X-Content-Type-Options": "nosniff"})

@app.post("/api/chat")
async def chat(req: Request):
    d = await req.json()
    agent = db.agent(d.get("agent_id") or "") or db.main_agent()
    msg = (d.get("message") or "").strip()
    atts = [m for m in (UPL.get(x) for x in (d.get("attachments") or [])[:10]) if m]
    mode = "regenerate" if d.get("regenerate") else "edit" if d.get("edit") else "new"
    conv = db.conv(d.get("conv_id") or "")
    if mode != "new":
        if not conv: raise HTTPException(400, "nothing to " + mode)
        if any(r["conv"] == conv["id"] for r in RUN_CONV.values()): raise HTTPException(409, "Wait for the current answer to finish (or press Stop).")
        ms = db.msgs(conv["id"]); lu = next((m for m in reversed(ms) if m["role"] == "user"), None)
        if not lu: raise HTTPException(400, "no message to " + mode)
        if mode == "regenerate":
            db.delete_msgs_from(conv["id"], lu["id"] + 1); msg = lu["content"]
        else:
            if not msg: raise HTTPException(400, "empty message")
            atts = [m for m in (UPL.get(x["id"]) for x in (lu["meta"] or {}).get("attachments") or []) if m]
            db.delete_msgs_from(conv["id"], lu["id"])
    if not msg and atts: msg = "(see attached file" + ("s" if len(atts) > 1 else "") + ")"
    if not msg: raise HTTPException(400, "empty message")
    if not conv:
        conv = db.create_conv(agent["id"], msg[:48] + ("…" if len(msg) > 48 else ""))
    else:
        agent = db.agent(conv["agent_id"]) or agent
    if mode != "regenerate":
        db.add_msg(conv["id"], "user", msg, {"attachments": [{k: a.get(k) for k in ("id", "name", "kind", "size", "mime", "chars", "error")} for a in atts]} if atts else None)
    q: asyncio.Queue = asyncio.Queue(); rid = uuid.uuid4().hex[:10]
    async def emit(ev): await q.put(ev)
    if cfg.get("auto_memory", True) and mode != "regenerate":
        for kind, fact in auto_extract(msg):
            mid = learn.add(fact, kind, "auto")
            if mid: await q.put({"type": "memory_saved", "text": fact, "id": mid, "auto": True})
    async def work():
        try:
            await emit({"type": "start", "run_id": rid, "conv_id": conv["id"], "agent": agent["name"], "mode": mode})
            await runner.run(agent, conv["id"], emit)
        except asyncio.CancelledError:
            part = ((runner.turn.get(conv["id"]) or {}).get("partial") or "").rstrip()
            db.add_msg(conv["id"], "assistant", (part + "\n\n" if part else "") + "_(stopped)_", {"agent": agent["name"], "stopped": True})
            await q.put({"type": "stopped", "kept": len(part)})
        except ProviderError as e: await q.put({"type": "error", "text": f"Model server error — {e}"})
        except Exception as e: await q.put({"type": "error", "text": f"{type(e).__name__}: {e}"})
        finally: await q.put(None); RUNS.pop(rid, None); RUN_CONV.pop(rid, None)
    RUN_CONV[rid] = {"conv": conv["id"]}
    RUNS[rid] = asyncio.create_task(work())
    async def gen():
        while True:
            ev = await q.get()
            if ev is None: yield "data: " + json.dumps({"type": "done", "conv_id": conv["id"]}) + "\n\n"; break
            yield "data: " + json.dumps(ev, default=str) + "\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

# ---------------- batch jobs (1.4) ----------------
def _bt(fn, *a):
    try: return fn(*a)
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as e: raise HTTPException(400, str(e) or type(e).__name__)

@app.post("/api/batch/upload")
async def batch_upload(req: Request):
    from urllib.parse import unquote as _uq
    body = await req.body()
    if not body: raise HTTPException(400, "empty file")
    if len(body) > 200 * 1024 * 1024: raise HTTPException(413, "File too large (max 200 MB).")
    try: return await asyncio.to_thread(BT.save_input, req.headers.get("X-Set") or "", _uq(req.headers.get("X-Filename") or "file"), body)
    except Exception as e: raise HTTPException(400, f"Could not read that file: {e}")

@app.post("/api/batch/preview")
async def batch_preview(req: Request): return await asyncio.to_thread(_bt, batch.preview, await req.json())

@app.post("/api/batch/estimate")
async def batch_estimate(req: Request):
    try: return await batch.estimate(await req.json())
    except ValueError as e: raise HTTPException(400, str(e))
    except Exception as e: raise HTTPException(502, f"Test item failed: {e}")

@app.post("/api/batch/jobs")
async def batch_create(req: Request):
    d = await req.json()
    if d.get("approved") is not True: raise HTTPException(400, "Starting a batch job needs your permission first.")
    spec = d.get("spec") or {}
    j = await asyncio.to_thread(_bt, batch.create, spec, d.get("job_id"), d.get("name"), d.get("estimate"))
    db.audit("Batch", "start_batch", {"job": j["id"], "items": j["total"], "prompt": (j["spec"].get("prompt") or "")[:300], "output": j["output_dir"]}, "allowed")
    return j

@app.get("/api/batch/jobs")
async def batch_jobs(): return {"jobs": batch.jobs(), "busy": batch.busy()}

@app.get("/api/batch/jobs/{jid}")
async def batch_job(jid: str, log: int = 60):
    j = batch.job(jid, log=min(500, log))
    if not j: raise HTTPException(404, "no such job")
    return j

@app.get("/api/batch/jobs/{jid}/items")
async def batch_items(jid: str, status: str = "", offset: int = 0, limit: int = 50): return batch.items(jid, status or None, offset, min(500, limit))

@app.post("/api/batch/jobs/{jid}/{action}")
async def batch_action(jid: str, action: str):
    if action not in ("pause", "resume", "cancel"): raise HTTPException(404, "unknown action")
    ok = getattr(batch, action)(jid)
    if not ok: raise HTTPException(409, f"Can't {action} this job right now.")
    return batch.job(jid)

@app.delete("/api/batch/jobs/{jid}")
async def batch_delete(jid: str): batch.delete(jid); return {"ok": True}

@app.get("/api/batch/jobs/{jid}/download/{name}")
async def batch_download(jid: str, name: str, rebuild: int = 1):
    from fastapi.responses import FileResponse
    if not batch.job(jid): raise HTTPException(404, "no such job")
    if rebuild:
        try: await asyncio.to_thread(batch.build_outputs, jid)
        except Exception as e: raise HTTPException(500, f"Could not build the files: {e}")
    if name not in BT.output_files(jid): raise HTTPException(404, "file not found")
    return FileResponse(BT.job_dir(jid) / "output" / name, media_type="application/octet-stream", filename=name, headers={"X-Content-Type-Options": "nosniff"})

@app.get("/api/batch/templates")
async def batch_templates(): return {"templates": batch.templates()}
@app.post("/api/batch/templates")
async def batch_template_save(req: Request): return _bt(batch.save_template, await req.json())
@app.delete("/api/batch/templates/{tid}")
async def batch_template_del(tid: int): batch.delete_template(tid); return {"ok": True}


# ---------------- Builder (1.6.0, deterministic, no AI) ----------------
def _builder_ok(d, action, detail):
    if d.get("approved") is not True:
        raise HTTPException(400, "This action needs your permission first.")
    db.audit("Builder", action, detail, "allowed")

def _be(fn, *a, **k):
    try:
        return fn(*a, **k)
    except BD.BuilderError as e:
        raise HTTPException(400, str(e))

@app.get("/api/builder/templates")
async def builder_templates():
    return {"templates": [BD.public_tpl(t) for t in BD.list_templates()]}

@app.get("/api/builder/templates/{tid}")
async def builder_template(tid: str):
    return BD.public_tpl(_be(BD.get_template, tid))

@app.get("/api/builder/snippets")
async def builder_snippets():
    return {"snippets": BD.list_snippets()}

@app.get("/api/builder/tools")
async def builder_tools():
    return await asyncio.to_thread(BD.detect_tools)

@app.get("/api/builder/default-folder")
async def builder_default_folder(name: str = "project"):
    return {"folder": BD.default_folder(name)}

@app.get("/api/builder/projects")
async def builder_projects():
    return {"projects": BD.list_projects()}

@app.get("/api/builder/projects/{pid}")
async def builder_project(pid: str):
    return _be(BD.get_project, pid)

@app.delete("/api/builder/projects/{pid}")
async def builder_forget(pid: str):
    """Remove from the list only; does not delete files on disk."""
    _be(BD.forget_project, pid)
    return {"ok": True}

@app.post("/api/builder/preview")
async def builder_preview(req: Request):
    d = await req.json()
    return _be(BD.preview_files, d.get("template"), d.get("name"), d.get("options") or {}, d.get("folder"))

@app.post("/api/builder/generate")
async def builder_generate(req: Request):
    d = await req.json()
    prev = _be(BD.preview_files, d.get("template"), d.get("name"), d.get("options") or {}, d.get("folder"))
    _builder_ok(d, "builder_generate", {"template": d.get("template"), "folder": prev["folder"], "files": [f["path"] for f in prev["files"]]})
    return await asyncio.to_thread(lambda: _be(BD.generate, d.get("template"), d.get("name"), d.get("options") or {}, d.get("folder")))

@app.get("/api/builder/projects/{pid}/tree")
async def builder_tree(pid: str):
    return {"tree": _be(BD.tree, pid)}

@app.get("/api/builder/projects/{pid}/file")
async def builder_read(pid: str, path: str):
    text, mtime = _be(BD.read_file, pid, path)
    return {"path": path, "content": text, "mtime": mtime}

@app.post("/api/builder/projects/{pid}/file")
async def builder_write(pid: str, req: Request):
    d = await req.json()
    rel = d.get("path") or ""
    _builder_ok(d, "builder_write", {"project": pid, "path": rel})
    return _be(BD.write_file, pid, rel, d.get("content") or "")

@app.post("/api/builder/projects/{pid}/snippet")
async def builder_snippet(pid: str, req: Request):
    d = await req.json()
    _builder_ok(d, "builder_snippet", {"project": pid, "path": d.get("path"), "snippet": d.get("snippet")})
    return _be(BD.insert_snippet, pid, d.get("path") or "", d.get("snippet") or "")

@app.post("/api/builder/projects/{pid}/save-template")
async def builder_save_tpl(pid: str, req: Request):
    d = await req.json()
    _builder_ok(d, "builder_save_template", {"project": pid, "id": d.get("id") or d.get("name")})
    return _be(BD.save_as_template, pid, d.get("id") or "", d.get("name") or "", d.get("description") or "")

@app.post("/api/builder/projects/{pid}/build")
async def builder_build(pid: str, req: Request):
    d = await req.json()
    proj = _be(BD.get_project, pid)
    _builder_ok(d, "builder_build", {"project": pid, "folder": proj["folder"]})
    return _be(BD.build_project, pid)

@app.post("/api/builder/projects/{pid}/run")
async def builder_run(pid: str, req: Request):
    d = await req.json()
    proj = _be(BD.get_project, pid)
    _builder_ok(d, "builder_run", {"project": pid, "folder": proj["folder"]})
    return _be(BD.run_project, pid)

@app.post("/api/builder/projects/{pid}/stop")
async def builder_stop(pid: str, req: Request):
    d = await req.json()
    return {"ok": BD.stop_run(d.get("job") or "")}

@app.post("/api/builder/projects/{pid}/package")
async def builder_package(pid: str, req: Request):
    d = await req.json()
    kind = d.get("kind") or "auto"
    if kind not in ("auto", "jar", "zip", "exe"):
        raise HTTPException(400, "kind must be auto, jar, zip or exe")
    proj = _be(BD.get_project, pid)
    _builder_ok(d, "builder_package", {"project": pid, "folder": proj["folder"], "kind": kind})
    return _be(BD.package_project, pid, kind)

@app.post("/api/builder/projects/{pid}/serve")
async def builder_serve(pid: str, req: Request):
    d = await req.json()
    proj = _be(BD.get_project, pid)
    _builder_ok(d, "builder_serve", {"project": pid, "folder": proj["folder"], "bind": "127.0.0.1"})
    return _be(BD.start_site, pid)

@app.post("/api/builder/projects/{pid}/serve/stop")
async def builder_serve_stop(pid: str, req: Request):
    await req.json()
    return {"ok": BD.stop_site(pid)}

@app.get("/api/builder/jobs/{jid}")
async def builder_job(jid: str):
    j = BD.job_view(jid)
    if not j: raise HTTPException(404, "no such job")
    return j

@app.get("/api/builder/projects/{pid}/download/{name}")
async def builder_download(pid: str, name: str):
    from fastapi.responses import FileResponse
    root = _be(BD.project_root, pid)
    path = _be(BD.safe_join, root / "dist", name)
    if not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name,
                        headers={"X-Content-Type-Options": "nosniff"})


# ---------------- setup wizard / Ollama management ----------------
OURL = lambda: cfg.get("ollama_url") or "http://localhost:11434"

def _approved(d, action, detail):
    if d.get("approved") is not True: raise HTTPException(400, "This action needs your permission first.")
    db.audit("Setup Wizard", action, detail, "allowed")

@app.get("/api/setup/hardware")
async def setup_hardware():
    import os
    if os.environ.get("JIV_FAKE_HW"):   # testing/screenshots: load a hardware profile from JSON
        hw = json.loads(Path(os.environ["JIV_FAKE_HW"]).read_text())
    else:
        hw = await asyncio.to_thread(HW.detect)
    return {"hardware": hw, "recommendation": MC.recommend(hw)}

@app.get("/api/catalog")
async def catalog(): return MC.CATALOG

@app.post("/api/setup/deny")
async def setup_deny(req: Request):
    d = await req.json(); db.audit("Setup Wizard", d.get("action", "?"), d.get("detail", {}), "denied"); return {"ok": True}

@app.get("/api/ollama/status")
async def ollama_status(): return await OM.status(OURL())

@app.post("/api/ollama/install")
async def ollama_install(req: Request):
    d = await req.json(); method = d.get("method", "installer")
    if method not in ("installer", "winget", "brew", "zip"): raise HTTPException(400, "bad method")
    detail = {"winget": " ".join(OM.WINGET_CMD), "brew": "brew install ollama && brew services start ollama", "zip": OM.MAC_ZIP_URL}.get(method, OM.INSTALLER_URL)
    _approved(d, "install_ollama", {"method": method, "mode": OM.INSTALL_MODE, "detail": detail})
    j = OM.new_job("install", method=method); OM.start(OM.install_job, j, OURL(), method); return OM.public_job(j)

@app.post("/api/ollama/start")
async def ollama_start(req: Request):
    d = await req.json(); _approved(d, "start_ollama", {"command": "ollama serve"})
    ok, msg = await OM.ensure_running(OURL()); return {"ok": ok, "message": msg}

@app.get("/api/ollama/models")
async def ollama_models():
    try: return {"ok": True, "models": await OM.list_models(OURL())}
    except Exception as e: return {"ok": False, "error": str(e)[:200], "models": []}

@app.post("/api/ollama/pull")
async def ollama_pull(req: Request):
    d = await req.json(); model = (d.get("model") or "").strip()
    if not model or any(ch in model for ch in " \"\n;&|"): raise HTTPException(400, "bad model name")
    _approved(d, "pull_model", {"model": model})
    for j in OM.JOBS.values():
        if j["kind"] == "pull" and j.get("model") == model and j["status"] == "running": return OM.public_job(j)
    j = OM.new_job("pull", model=model); OM.start(OM.pull_job, j, OURL(), model); return OM.public_job(j)

@app.post("/api/ollama/delete")
async def ollama_delete(req: Request):
    d = await req.json(); _approved(d, "delete_model", {"model": d.get("model")})
    try: await OM.delete_model(OURL(), d["model"]); return {"ok": True}
    except Exception as e: raise HTTPException(400, str(e))

@app.get("/api/jobs/{jid}")
async def job(jid: str):
    j = OM.JOBS.get(jid)
    if not j: raise HTTPException(404, "no such job")
    return OM.public_job(j)

@app.post("/api/jobs/{jid}/cancel")
async def job_cancel(jid: str): return {"ok": OM.cancel(jid)}

@app.post("/api/setup/finish")
async def setup_finish(req: Request):
    d = await req.json()
    upd = {"setup_done": True}
    if d.get("model"):
        upd.update(provider="ollama", base_url=OURL().rstrip("/") + "/v1", model=d["model"], api_key="")
    cfg.update(upd); return cfg.public()

# ---------------- updates ----------------
import os, re, sys, shutil, subprocess, time as _time
from . import __version__, updater as UP
from .updater import UpdateError
UPDATE_NOTICE = {}
LAST_ACTIVITY = {"t": _time.time()}
FROZEN = getattr(sys, "frozen", False)

@app.middleware("http")
async def activity(request: Request, call_next):
    if request.url.path.startswith("/api/chat"): LAST_ACTIVITY["t"] = _time.time()
    return await call_next(request)

@app.get("/health")
async def health():
    db.q("SELECT 1")
    return {"ok": True, "version": __version__}

def _restart_supported(): return bool(os.environ.get("JIV_LAUNCH")) and not FROZEN

def schedule_restart(expect, previous, backup_name):
    """Hand over to the stand-alone relauncher (copied outside the app folder), then exit."""
    if not _restart_supported(): return False
    upd = data_dir() / "updates"; upd.mkdir(parents=True, exist_ok=True)
    rl = upd / "relaunch.py"; shutil.copyfile(Path(__file__).parent / "relaunch.py", rl)
    job = {"port": int(os.environ.get("JIV_PORT", cfg.get("port", 8765))), "cmd": json.loads(os.environ["JIV_LAUNCH"]),
           "app_dir": str(UP.APP_DIR), "expect": expect, "previous": previous, "timeout": int(os.environ.get("JIV_HEALTH_TIMEOUT", 30)),
           "backup_app": str(UP.backups_dir() / backup_name / "app") if backup_name else "", "state_file": str(UP.state_path()),
           "log": str(upd / "relaunch.log"), "env": {"JIV_DATA_DIR": os.environ.get("JIV_DATA_DIR", "")} if os.environ.get("JIV_DATA_DIR") else {}}
    jp = upd / "relaunch-job.json"; jp.write_text(json.dumps(job, indent=2))
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(upd))
    if os.name == "nt": kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else: kw["start_new_session"] = True
    subprocess.Popen([sys.executable, str(rl), str(jp)], **kw)
    asyncio.get_running_loop().call_later(0.8, os._exit, 0)
    return True

LAST_INSTALLER = {}

@app.post("/api/updates/installer")
async def updates_installer(req: Request):
    """Big update (new embedded Python etc.): download the JivSetup.exe found by the last GitHub check and run it."""
    global LAST_INSTALLER
    d = await req.json(); inst = LAST_INSTALLER
    if not inst: raise HTTPException(400, "No installer found. Click 'Check for updates' first.")
    if d.get("approved") is not True:
        db.audit("user", "run_installer", {"name": inst["name"]}, "denied"); raise HTTPException(400, "This needs your permission first.")
    import httpx
    dest = data_dir() / "updates" / "installers"; dest.mkdir(parents=True, exist_ok=True)
    path = dest / re.sub(r"[^A-Za-z0-9._-]", "_", inst["name"])
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as c:
        async with c.stream("GET", inst["url"]) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                async for chunk in r.aiter_bytes(): f.write(chunk)
    if path.read_bytes()[:2] != b"MZ": path.unlink(); raise HTTPException(400, "Downloaded file is not a Windows program.")
    run = os.name == "nt" or os.environ.get("JIV_INSTALLER_RUN_CMD")
    db.audit("user", "run_installer", {"name": inst["name"], "version": inst.get("version"), "path": str(path)}, "allowed",
             "started" if run else "downloaded only (not Windows)")
    if not run: return {"ok": True, "path": str(path), "started": False, "message": f"Downloaded to {path}. Run it on Windows to upgrade."}
    UP.make_backup(f"before installer {inst.get('version')}", __version__)
    # /RELAUNCH: the installer starts Jiv again when it is done. JIV_INSTALLER_ARGS (JSON) lets tests add /S.
    cmd = (json.loads(os.environ["JIV_INSTALLER_RUN_CMD"]) if os.environ.get("JIV_INSTALLER_RUN_CMD") else []) + [str(path)] + \
          json.loads(os.environ.get("JIV_INSTALLER_ARGS") or '["/RELAUNCH"]')
    kw = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    asyncio.get_running_loop().call_later(1.0, os._exit, 0)  # quit so the installer can replace our files
    return {"ok": True, "path": str(path), "started": True, "message": "The installer is starting; Jiv will close now."}

def _pkg_view(meta):
    out = dict(meta)
    try:
        d, _ = UP.staged(meta["id"])
        if meta.get("manifest"): out["diff"] = UP.diff_preview(d / "package.jivupdate")
    except Exception: pass
    return out

@app.get("/api/updates")
async def updates_state():
    return {"version": __version__, "staged": UP.staged_list(), "backups": UP.list_backups(), "state": UP.read_state(),
            "repo": UP.effective_repo(cfg.get("update_repo")), "repo_is_default": not (cfg.get("update_repo") or "").strip(),
            "default_repo": UP.DEFAULT_REPO, "auto_install_signed": cfg.get("auto_install_signed"),
            "restart_supported": _restart_supported(), "frozen": FROZEN, "notice": UPDATE_NOTICE, "layout": UP.layout(), "installer": LAST_INSTALLER, "app_dir": str(UP.APP_DIR)}

@app.get("/api/updates/staged/{sid}")
async def updates_one(sid: str):
    try: return _pkg_view(UP.staged(sid)[1])
    except UpdateError as e: raise HTTPException(404, str(e))

@app.post("/api/updates/upload")
async def updates_upload(req: Request):
    body = await req.body()
    if not body: raise HTTPException(400, "empty file")
    if len(body) > UP.MAX_PACKAGE: raise HTTPException(400, "file too large")
    return _pkg_view(UP.stage_file(body, origin="upload", extra={"filename": req.headers.get("x-filename", "update.jivupdate")[:120]}))

@app.post("/api/updates/check")
async def updates_check():
    repo = UP.effective_repo(cfg.get("update_repo"))  # empty = the official public repo
    try:
        global LAST_INSTALLER
        r = await UP.check_github(repo)
        LAST_INSTALLER = r.get("installer") or {}
        if r.get("staged"): r["staged"] = _pkg_view(r["staged"])
        return {"configured": True, **r}
    except UpdateError as e: return {"configured": True, "error": str(e)}
    except Exception as e: return {"configured": True, "error": f"Could not reach GitHub: {type(e).__name__}: {str(e)[:200]}"}

@app.post("/api/updates/discard/{sid}")
async def updates_discard(sid: str):
    try: UP.discard(sid); return {"ok": True}
    except UpdateError as e: raise HTTPException(400, str(e))

async def _apply(sid, advanced, actor="user"):
    d, meta = UP.staged(sid)
    if meta.get("origin") == "self" and not meta.get("checks_ok") and not advanced:
        raise UpdateError("this self-made update failed its checks; tick 'advanced' to apply anyway")
    info = UP.verify_package(d / "package.jivupdate")
    if info["requirements"]:
        ok, out = await asyncio.to_thread(UP.install_requirements, info["requirements"])
        db.audit(actor, "pip_install", {"id": sid, "packages": info["requirements"]}, "allowed" if ok else "error", out[-500:])
        if not ok: raise UpdateError("Installing the new Python packages failed, so nothing was changed:\n" + out[-1500:])
    res = await asyncio.to_thread(UP.apply_package, d / "package.jivupdate", advanced)
    db.audit(actor, "apply_update", {"id": sid, "version": res["version"], "signed": res["info"]["signed"],
                                     "files": [f["path"] for f in res["info"]["manifest"]["files"]]}, "allowed", f"backup {res['backup']['name']}")
    UP.update_staged_meta(sid, applied=_time.time())
    restarting = schedule_restart(res["version"], __version__, res["backup"]["name"])
    return {"ok": True, "version": res["version"], "backup": res["backup"]["name"], "restarting": restarting,
            "message": None if restarting else "Update installed. Please restart Jiv to finish."}

@app.post("/api/updates/apply/{sid}")
async def updates_apply(sid: str, req: Request):
    d = await req.json()
    if d.get("approved") is not True:
        db.audit("user", "apply_update", {"id": sid}, "denied"); raise HTTPException(400, "This needs your permission first.")
    try: return await _apply(sid, bool(d.get("advanced")))
    except UpdateError as e: raise HTTPException(400, str(e))

@app.post("/api/updates/deny/{sid}")
async def updates_deny(sid: str):
    db.audit("user", "apply_update", {"id": sid}, "denied"); return {"ok": True}

@app.post("/api/updates/rollback")
async def updates_rollback(req: Request):
    d = await req.json()
    if d.get("approved") is not True: raise HTTPException(400, "This needs your permission first.")
    name = d.get("backup") or (UP.list_backups()[0]["name"] if UP.list_backups() else "")
    try:
        target = next(b for b in UP.list_backups() if b["name"] == name)
        pre = await asyncio.to_thread(UP.make_backup, f"before rollback to {target['version']}", target["version"])
        await asyncio.to_thread(UP.restore_backup, name)
    except StopIteration: raise HTTPException(400, "backup not found")
    except UpdateError as e: raise HTTPException(400, str(e))
    db.audit("user", "rollback", {"backup": name, "version": target["version"]}, "allowed", f"saved current as {pre['name']}")
    UP.write_state(status="pending", from_version=__version__, to_version=target["version"], backup=pre["name"], rollback=True)
    restarting = schedule_restart(target["version"], __version__, pre["name"])
    return {"ok": True, "version": target["version"], "restarting": restarting}

@app.post("/api/updates/settings")
async def updates_settings(req: Request):
    d = await req.json(); upd = {}
    if "update_repo" in d:
        r = (d["update_repo"] or "").strip()
        if r:
            try: UP.parse_repo(r)
            except UpdateError as e: raise HTTPException(400, str(e))
        upd["update_repo"] = r
    if "auto_install_signed" in d: upd["auto_install_signed"] = bool(d["auto_install_signed"])
    if "update_test_cmd" in d: upd["update_test_cmd"] = str(d["update_test_cmd"])[:500]
    cfg.update(upd); return {"ok": True}

def _idle():
    return not RUNS and not perms.pending and not batch.busy() and _time.time() - LAST_ACTIVITY["t"] >= float(os.environ.get("JIV_IDLE_SECS", 120))

async def auto_update_loop():
    await asyncio.sleep(float(os.environ.get("JIV_UPDATE_FIRST_CHECK", 60)))
    while True:
        try:
            if cfg.get("auto_install_signed") and _restart_supported():
                r = await UP.check_github(UP.effective_repo(cfg.get("update_repo")))
                st = r.get("staged")
                if st and st.get("ok") and st.get("signed"):
                    UPDATE_NOTICE.clear(); UPDATE_NOTICE.update(id=st["id"], version=r["latest"], status="waiting_idle",
                        message=f"Signed update {r['latest']} downloaded — it will install and restart Jiv when you're idle.")
                    while not _idle(): await asyncio.sleep(5)
                    UPDATE_NOTICE["status"] = "installing"
                    await _apply(st["id"], False, actor="auto-update")
                elif st:
                    UPDATE_NOTICE.clear(); UPDATE_NOTICE.update(id=st["id"], version=r.get("latest"), status="needs_review",
                        message=f"Update {r.get('latest')} found but it is not signed by the Jiv key — review it on the Updates page.")
        except Exception as e:
            UPDATE_NOTICE.update(status="error", message=f"Auto-update check failed: {str(e)[:200]}")
        await asyncio.sleep(float(os.environ.get("JIV_UPDATE_INTERVAL", 6 * 3600)))

@app.on_event("startup")
async def _startup_batch(): batch.start()

@app.on_event("startup")
async def _startup_updates():
    st = UP.read_state()
    if st.get("status") == "pending" and st.get("to_version") == __version__ and not os.environ.get("JIV_LAUNCH"):
        UP.write_state(status="ok", message=f"Updated to {__version__}")
    asyncio.create_task(auto_update_loop())
