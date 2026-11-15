"""Ollama install / service / model management with progress jobs."""
import asyncio, json, os, shutil, subprocess, sys, tempfile, time, uuid
from pathlib import Path
import httpx

IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"
INSTALLER_URL = os.environ.get("JIV_OLLAMA_INSTALLER_URL", "https://ollama.com/download/OllamaSetup.exe")
INSTALL_MODE = os.environ.get("JIV_INSTALL_MODE") or ("windows" if IS_WIN else "mac" if IS_MAC else "linux")
WINGET_CMD = ["winget", "install", "-e", "--id", "Ollama.Ollama", "--silent", "--accept-source-agreements", "--accept-package-agreements"]
LINUX_CMD = "curl -fsSL https://ollama.com/install.sh | sh"
MAC_ZIP_URL = os.environ.get("JIV_OLLAMA_MAC_ZIP_URL", "https://ollama.com/download/Ollama-darwin.zip")
BREW_CMDS = [["brew", "install", "ollama"], ["brew", "services", "start", "ollama"]]

def brew_path():
    if os.environ.get("JIV_NO_BREW"): return None
    return shutil.which("brew") or next((p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.exists(p)), None)

def mac_apps_dirs():
    if os.environ.get("JIV_MAC_APPS_DIR"): return [Path(os.environ["JIV_MAC_APPS_DIR"])]
    return [Path("/Applications"), Path.home() / "Applications"]

def mac_app_path():
    for d in mac_apps_dirs():
        if (d / "Ollama.app").exists(): return d / "Ollama.app"
    return None

def mac_install_target():
    """/Applications if writable, else ~/Applications."""
    for d in mac_apps_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            if os.access(d, os.W_OK): return d
        except Exception: continue
    return Path.home() / "Applications"
NOWIN = {"creationflags": 0x08000000} if IS_WIN else {}

JOBS: dict = {}

def new_job(kind, **kw):
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"id": jid, "kind": kind, "status": "running", "phase": "", "completed": 0, "total": 0, "percent": 0.0,
                 "speed_mbps": 0.0, "eta_s": None, "log": [], "error": "", "started": time.time(), **kw}
    return JOBS[jid]

def public_job(j): return {k: v for k, v in j.items() if not k.startswith("_")}

def log(j, msg):
    j["log"].append(msg); j["log"] = j["log"][-60:]; j["phase"] = msg

class Speed:
    """Rolling-window throughput + ETA."""
    def __init__(self): self.pts = []
    def update(self, j, completed, total):
        now = time.time(); self.pts.append((now, completed)); self.pts = [p for p in self.pts if now - p[0] <= 5]
        j["completed"], j["total"] = completed, total
        j["percent"] = round(completed / total * 100, 1) if total else 0.0
        if len(self.pts) >= 2 and self.pts[-1][0] > self.pts[0][0]:
            bps = max(0, (self.pts[-1][1] - self.pts[0][1]) / (self.pts[-1][0] - self.pts[0][0]))
            j["speed_mbps"] = round(bps / 1e6, 2)
            j["eta_s"] = int((total - completed) / bps) if bps > 0 and total else None

# ---------------- detection ----------------
def find_binary():
    if os.environ.get("JIV_OLLAMA_BIN"): return os.environ["JIV_OLLAMA_BIN"] if os.path.exists(os.environ["JIV_OLLAMA_BIN"]) else None
    p = shutil.which("ollama")
    if p: return p
    cands = []
    if IS_WIN:
        la = os.environ.get("LOCALAPPDATA", "")
        cands = [os.path.join(la, "Programs", "Ollama", "ollama.exe"), r"C:\Program Files\Ollama\ollama.exe"]
    elif INSTALL_MODE == "mac":
        app = mac_app_path()
        cands = ([str(app / "Contents" / "Resources" / "ollama")] if app else []) + \
                ["/opt/homebrew/bin/ollama", "/usr/local/bin/ollama", str(Path.home() / "Applications/Ollama.app/Contents/Resources/ollama")]
    else:
        cands = ["/usr/local/bin/ollama", "/usr/bin/ollama", str(Path.home() / ".local/bin/ollama")]
    return next((c for c in cands if os.path.exists(c)), None)

async def version(url):
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(url.rstrip("/") + "/api/version"); r.raise_for_status(); return r.json().get("version", "?")
    except Exception: return None

async def status(url):
    v = await version(url); b = find_binary()
    return {"installed": bool(b or v), "binary": b, "running": bool(v), "version": v, "url": url, "install_mode": INSTALL_MODE,
            "installer_url": INSTALLER_URL, "winget_cmd": " ".join(WINGET_CMD), "linux_cmd": LINUX_CMD,
            "winget_available": bool(shutil.which("winget")) if IS_WIN else False,
            "brew_available": bool(brew_path()) if INSTALL_MODE == "mac" else False, "brew_cmd": " && ".join(" ".join(c) for c in BREW_CMDS),
            "mac_zip_url": MAC_ZIP_URL, "mac_target": str(mac_install_target()) if INSTALL_MODE == "mac" else "",
            "mac_app": str(mac_app_path() or "")}

async def ensure_running(url, wait=25):
    if await version(url): return True, "already running"
    if INSTALL_MODE == "mac" and IS_MAC and mac_app_path():
        subprocess.run(["open", "-g", "-a", str(mac_app_path())])          # menu-bar app starts the server
        for _ in range(wait * 4):
            await asyncio.sleep(0.25)
            if await version(url): return True, "launched Ollama.app"
    b = find_binary()
    if not b: return False, "Ollama is not installed"
    env = dict(os.environ)
    host = url.split("://", 1)[-1].rstrip("/")
    env["OLLAMA_HOST"] = host
    kw = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env)
    if IS_WIN: kw["creationflags"] = 0x08000000 | 0x00000008  # no window, detached
    else: kw["start_new_session"] = True
    subprocess.Popen([b, "serve"], **kw)
    for _ in range(wait * 4):
        await asyncio.sleep(0.25)
        if await version(url): return True, "started 'ollama serve'"
    return False, "Ollama did not start in time"

# ---------------- install ----------------
async def _download(j, url, dest):
    sp = Speed()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True) as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status(); total = int(r.headers.get("content-length") or 0); done = 0
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 16):
                    f.write(chunk); done += len(chunk); sp.update(j, done, total)
    return dest

async def _run_proc(j, args, shell=False):
    log(j, "$ " + (args if isinstance(args, str) else " ".join(args)))
    if shell: p = await asyncio.create_subprocess_shell(args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **NOWIN)
    else: p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **NOWIN)
    j["_proc"] = p
    async for line in p.stdout:
        t = line.decode("utf-8", "replace").strip()
        if t: j["log"].append(t[:300]); j["log"] = j["log"][-60:]
    return await p.wait()

async def install_job(j, url, method="installer"):
    try:
        if method == "winget":
            log(j, "Installing with winget…"); rc = await _run_proc(j, WINGET_CMD)
            if rc != 0: raise RuntimeError(f"winget exited with code {rc}")
        elif INSTALL_MODE == "windows":
            dest = Path(tempfile.gettempdir()) / "JivDesktop" / "OllamaSetup.exe"; dest.parent.mkdir(parents=True, exist_ok=True)
            log(j, f"Downloading {INSTALLER_URL}"); await _download(j, INSTALLER_URL, dest)
            if not IS_WIN: os.chmod(dest, 0o755)
            log(j, "Running installer silently…")
            rc = await _run_proc(j, [str(dest), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"])
            if rc != 0:
                log(j, f"Silent install returned {rc}; opening the normal installer — please follow its steps.")
                rc = await _run_proc(j, [str(dest)])
                if rc != 0: raise RuntimeError(f"Installer exited with code {rc}")
        elif INSTALL_MODE == "linux":
            log(j, "Running the official Linux install script (may ask for sudo)…")
            rc = await _run_proc(j, LINUX_CMD, shell=True)
            if rc != 0: raise RuntimeError(f"install.sh exited with code {rc} (it needs sudo; run it in a terminal)")
        elif INSTALL_MODE == "mac" and (method == "brew" or (method == "installer" and brew_path())):
            brew = brew_path()
            if not brew: raise RuntimeError("Homebrew not found")
            log(j, "Installing with Homebrew…"); j["percent"] = 10.0
            rc = await _run_proc(j, [brew, "install", "ollama"])
            if rc != 0: raise RuntimeError(f"brew install exited with code {rc}")
            j["percent"] = 80.0
            rc = await _run_proc(j, [brew, "services", "start", "ollama"])
            if rc != 0: log(j, "brew services failed; will start 'ollama serve' instead")
        elif INSTALL_MODE == "mac":
            await install_mac_zip(j)
        else:
            raise RuntimeError(f"Unsupported install mode {INSTALL_MODE}")
        log(j, "Starting Ollama service…")
        for _ in range(40):
            if find_binary() or await version(url): break
            await asyncio.sleep(0.5)
        ok, msg = await ensure_running(url); log(j, msg)
        if not ok: raise RuntimeError(msg)
        j["percent"] = 100.0; j["status"] = "done"; log(j, "Ollama is installed and running ✓")
    except asyncio.CancelledError:
        p = j.get("_proc")
        if p and p.returncode is None: p.kill()
        j["status"] = "cancelled"; log(j, "Cancelled")
    except Exception as e:
        j["status"] = "error"; j["error"] = f"{type(e).__name__}: {e}"; log(j, j["error"])

async def install_mac_zip(j):
    tmp = Path(tempfile.gettempdir()) / "JivDesktop"; tmp.mkdir(parents=True, exist_ok=True)
    zpath = tmp / "Ollama-darwin.zip"
    log(j, f"Downloading {MAC_ZIP_URL}"); await _download(j, MAC_ZIP_URL, zpath)
    target = mac_install_target(); log(j, f"Unzipping Ollama.app into {target}")
    if (target / "Ollama.app").exists(): shutil.rmtree(target / "Ollama.app")
    if shutil.which("ditto"):     # preserves symlinks, permissions and code signature
        rc = await _run_proc(j, ["ditto", "-x", "-k", str(zpath), str(target)])
        if rc != 0: raise RuntimeError(f"ditto exited with code {rc}")
    else:
        import zipfile
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                out = z.extract(info, target)
                mode = info.external_attr >> 16
                if mode & 0o777: os.chmod(out, mode & 0o777)
    app = target / "Ollama.app"
    if not app.exists(): raise RuntimeError("Ollama.app not found in the download")
    if shutil.which("xattr"):
        await _run_proc(j, ["xattr", "-dr", "com.apple.quarantine", str(app)])
    else: log(j, "xattr not available — skipping quarantine removal")
    j["percent"] = 100.0; log(j, "Launching Ollama.app…")

# ---------------- models ----------------
async def list_models(url):
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(url.rstrip("/") + "/api/tags"); r.raise_for_status()
    return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 2), "modified": m.get("modified_at", ""),
             "family": (m.get("details") or {}).get("family", ""), "params": (m.get("details") or {}).get("parameter_size", ""),
             "quant": (m.get("details") or {}).get("quantization_level", "")} for m in r.json().get("models", [])]

async def delete_model(url, name):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request("DELETE", url.rstrip("/") + "/api/delete", json={"model": name, "name": name})
    if r.status_code >= 400: raise RuntimeError(r.text[:300])
    return True

async def pull_job(j, url, model):
    sp, layers = Speed(), {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10)) as c:
            async with c.stream("POST", url.rstrip("/") + "/api/pull", json={"model": model, "name": model, "stream": True}) as r:
                if r.status_code >= 400: raise RuntimeError((await r.aread()).decode()[:300])
                async for line in r.aiter_lines():
                    if not line.strip(): continue
                    ev = json.loads(line)
                    if ev.get("error"): raise RuntimeError(ev["error"])
                    st = ev.get("status", "")
                    if ev.get("digest") and ev.get("total"):
                        layers[ev["digest"]] = (ev.get("completed", 0), ev["total"])
                        sp.update(j, sum(a for a, _ in layers.values()), sum(b for _, b in layers.values()))
                        j["phase"] = "Downloading"
                    elif st: log(j, st)
                    if st == "success": j["status"] = "done"; j["percent"] = 100.0; j["eta_s"] = 0
        if j["status"] != "done": raise RuntimeError("Pull ended without success")
    except asyncio.CancelledError:
        j["status"] = "cancelled"; log(j, "Paused — press Resume to continue (already-downloaded parts are kept)")
    except Exception as e:
        j["status"] = "error"; j["error"] = str(e)[:400]; log(j, "Error: " + j["error"])

def start(coro_fn, j, *a):
    j["_task"] = asyncio.create_task(coro_fn(j, *a)); return j

def cancel(jid):
    j = JOBS.get(jid)
    if j and j.get("_task") and not j["_task"].done(): j["_task"].cancel(); return True
    return False
