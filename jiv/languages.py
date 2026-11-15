"""Language manager + code runner.
Detects runtimes, installs missing ones (winget on Windows, Homebrew on macOS; always behind a permission card),
and runs code in <data>/workspace with a timeout."""
import asyncio, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path
from .config import data_dir

IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"
NOWIN = {"creationflags": 0x08000000} if IS_WIN else {}

# id: label, exes to look for, version args, winget id, brew formula, file name, [compile], run
RUNTIMES = {
 "python":     dict(label="Python", exes=["python", "python3", "py"], ver=["--version"], winget="Python.Python.3.12", brew="python@3.12",
                    file="main.py", run=["{exe}", "-u", "{file}"]),
 "javascript": dict(label="Node.js (JavaScript)", exes=["node"], ver=["--version"], winget="OpenJS.NodeJS.LTS", brew="node",
                    file="main.js", run=["{exe}", "{file}"]),
 "java":       dict(label="Java (JDK 21)", exes=["java"], ver=["-version"], winget="EclipseAdoptium.Temurin.21.JDK", brew="openjdk@21",
                    file="Main.java", run=["{exe}", "{file}"]),  # JDK 11+ runs a single source file directly
 "c":          dict(label="C (gcc)", exes=["gcc", "clang", "cc"], ver=["--version"], winget="BrechtSanders.WinLibs.POSIX.UCRT", brew="gcc",
                    file="main.c", compile=["{exe}", "{file}", "-O2", "-o", "{bin}", "-lm"], run=["{bin}"]),
 "cpp":        dict(label="C++ (g++)", exes=["g++", "clang++", "c++"], ver=["--version"], winget="BrechtSanders.WinLibs.POSIX.UCRT", brew="gcc",
                    file="main.cpp", compile=["{exe}", "{file}", "-O2", "-std=c++17", "-o", "{bin}"], run=["{bin}"]),
 "go":         dict(label="Go", exes=["go"], ver=["version"], winget="GoLang.Go", brew="go", file="main.go", run=["{exe}", "run", "{file}"]),
 "rust":       dict(label="Rust (rustc)", exes=["rustc"], ver=["--version"], winget="Rustlang.Rustup", brew="rust",
                    file="main.rs", compile=["{exe}", "-O", "{file}", "-o", "{bin}"], run=["{bin}"]),
 "csharp":     dict(label="C# (.NET 8 SDK)", exes=["dotnet"], ver=["--version"], winget="Microsoft.DotNet.SDK.8", brew="dotnet-sdk", brew_cask=True,
                    file="Program.cs", run=["{exe}", "run", "--project", "{dir}"]),
 "php":        dict(label="PHP", exes=["php"], ver=["--version"], winget="PHP.PHP.8.3", brew="php", file="main.php", run=["{exe}", "{file}"]),
 "ruby":       dict(label="Ruby", exes=["ruby"], ver=["--version"], winget="RubyInstallerTeam.Ruby.3.3", brew="ruby", file="main.rb", run=["{exe}", "{file}"]),
 "powershell": dict(label="PowerShell", exes=["pwsh", "powershell"], ver=["-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                    winget="Microsoft.PowerShell", brew="powershell", brew_cask=True, file="main.ps1",
                    run=["{exe}", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "{file}"]),
}
ALIASES = {"py": "python", "python3": "python", "js": "javascript", "node": "javascript", "nodejs": "javascript", "c++": "cpp", "g++": "cpp",
           "golang": "go", "rs": "rust", "cs": "csharp", "c#": "csharp", "dotnet": "csharp", "ps": "powershell", "ps1": "powershell", "pwsh": "powershell", "rb": "ruby"}

def norm_lang(lang):
    l = str(lang or "").strip().lower()
    return ALIASES.get(l, l)

def workspace():
    p = data_dir() / "workspace"; p.mkdir(parents=True, exist_ok=True); return p

def refresh_path():
    """After winget installs something, pick up the new PATH from the registry (this process still has the old one)."""
    if not IS_WIN: return
    try:
        import winreg
        parts = []
        for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"), (winreg.HKEY_CURRENT_USER, "Environment")):
            try:
                with winreg.OpenKey(root, key) as k: parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
            except OSError: pass
        cur = os.environ.get("PATH", "").split(os.pathsep)
        for p in os.pathsep.join(parts).split(os.pathsep):
            if p and p not in cur: cur.append(p)
        os.environ["PATH"] = os.pathsep.join(cur)
    except Exception: pass

EXTRA_DIRS_WIN = [r"%ProgramFiles%\nodejs", r"%ProgramFiles%\Go\bin", r"%USERPROFILE%\.cargo\bin", r"%ProgramFiles%\dotnet",
                  r"%LOCALAPPDATA%\Programs\Python\Python312", r"%ProgramFiles%\PowerShell\7"]

def _which(exe):
    p = shutil.which(exe)
    if p and IS_WIN and "windowsapps" in p.lower() and exe.startswith(("python", "py")): return None  # Microsoft Store stub, not a real Python
    if not p and IS_WIN:
        for d in EXTRA_DIRS_WIN:
            for cand in (Path(os.path.expandvars(d)) / (exe + ".exe"),):
                if cand.exists(): return str(cand)
    if not p and exe == "java":
        jh = os.environ.get("JAVA_HOME")
        if jh and (Path(jh) / "bin" / ("java.exe" if IS_WIN else "java")).exists(): return str(Path(jh) / "bin" / ("java.exe" if IS_WIN else "java"))
        if IS_MAC and Path("/opt/homebrew/opt/openjdk@21/bin/java").exists(): return "/opt/homebrew/opt/openjdk@21/bin/java"
    return p

def builtin_python():
    """Jiv's own Python (the embedded one in the Windows installer, or the venv) - always usable for Python code."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists(): exe = exe.parent / "python.exe"
    return str(exe)

def _version(exe, args):
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=15, **NOWIN)
        out = (r.stdout + r.stderr).strip().splitlines()
        return (out[0] if out else "")[:120] if r.returncode == 0 or out else ""
    except Exception: return ""

def detect_one(lid):
    rt = RUNTIMES[lid]
    for exe in rt["exes"]:
        p = _which(exe)
        if p:
            v = _version(p, rt["ver"])
            if v or lid != "python": return {"id": lid, "installed": True, "path": p, "version": v, "builtin": False}
    if lid == "python":
        return {"id": lid, "installed": True, "path": builtin_python(), "version": "Jiv's built-in Python " + sys.version.split()[0], "builtin": True}
    return {"id": lid, "installed": False, "path": "", "version": "", "builtin": False}

_CACHE = {"t": 0, "data": None}
def detect(force=False):
    if not force and _CACHE["data"] and time.time() - _CACHE["t"] < 60: return _CACHE["data"]
    refresh_path()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex: found = dict(zip(RUNTIMES, ex.map(detect_one, RUNTIMES)))
    out = []
    for lid, rt in RUNTIMES.items():
        d = found[lid]
        d.update(label=rt["label"], winget=rt["winget"], brew=rt["brew"], install_cmd=" ".join(install_cmd(lid) or []),
                 can_install=bool(install_cmd(lid)))
        out.append(d)
    _CACHE.update(t=time.time(), data=out)
    return out

def winget_exe():
    return os.environ.get("JIV_WINGET") or shutil.which("winget")

def install_cmd(lid):
    rt = RUNTIMES.get(lid)
    if not rt: return None
    if IS_WIN or os.environ.get("JIV_WINGET"):
        w = winget_exe()
        return [w or "winget", "install", "-e", "--id", rt["winget"], "--silent", "--accept-source-agreements", "--accept-package-agreements"]
    if IS_MAC:
        b = shutil.which("brew") or ("/opt/homebrew/bin/brew" if Path("/opt/homebrew/bin/brew").exists() else None)
        if not b: return None
        return [b, "install"] + (["--cask"] if rt.get("brew_cask") else []) + [rt["brew"]]
    return None  # Linux: use your package manager

JOBS = {}
async def install(lid):
    """Run the installer; returns a job id. Caller must already have the user's permission."""
    cmd = install_cmd(lid)
    if not cmd: raise ValueError("No installer available on this system (winget/Homebrew not found).")
    jid = uuid.uuid4().hex[:10]; job = JOBS[jid] = {"id": jid, "lang": lid, "status": "running", "log": ["$ " + " ".join(cmd)], "started": time.time()}
    async def work():
        try:
            p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **NOWIN)
            async for line in p.stdout:
                t = line.decode("utf-8", "replace").rstrip()
                t = re.sub(r"[\x00-\x08\x0b-\x1f]|\s*[-\\|/]\s*$", "", t)
                if t.strip(): job["log"].append(t[-300:]); job["log"] = job["log"][-200:]
            rc = await p.wait()
            refresh_path(); _CACHE["data"] = None
            d = detect_one(lid)
            if lid == "rust" and IS_WIN and not d["installed"]:
                job["log"].append("Rustup installed. Open a new terminal (or restart Jiv) and run 'rustup default stable' if rustc is still missing.")
            job.update(status="done" if (rc == 0 or d["installed"]) else "error", rc=rc, detected=d)
            if job["status"] == "done" and not d["installed"]: job["log"].append("Installed, but Jiv can't see it yet: restart Jiv so it picks up the new PATH.")
        except Exception as e: job.update(status="error", error=f"{type(e).__name__}: {e}")
    job["task"] = asyncio.create_task(work())
    return jid

def job_view(jid):
    j = JOBS.get(jid)
    return {k: v for k, v in j.items() if k != "task"} if j else None

# ---------------- run code ----------------
def _clip(s, n=6000):
    s = s or ""; return s if len(s) <= n else s[:n // 2] + f"\n…[{len(s) - n} chars cut]…\n" + s[-n // 2:]

def _java_file(code):
    m = re.search(r"public\s+(?:final\s+)?class\s+(\w+)", code)
    return (m.group(1) if m else "Main") + ".java"

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework>
<ImplicitUsings>enable</ImplicitUsings><Nullable>disable</Nullable></PropertyGroup></Project>"""

async def run_code(language, code, stdin="", timeout=30, args=None):
    """Write `code` to a fresh workspace folder, compile if needed, run it. Returns a dict."""
    lid = norm_lang(language)
    if lid not in RUNTIMES: return {"ok": False, "stage": "setup", "error": f"Unknown language '{language}'. Supported: {', '.join(RUNTIMES)}"}
    rt = RUNTIMES[lid]; d = detect_one(lid)
    if not d["installed"]:
        return {"ok": False, "stage": "setup", "missing": lid,
                "error": f"{rt['label']} is not installed. Open the Languages page ({{}} Languages) to install it" + (f" (winget id {rt['winget']})." if IS_WIN else ".")}
    timeout = max(3, min(int(timeout or 30), 300))
    run_dir = workspace() / (time.strftime("%Y%m%d-%H%M%S-") + lid + "-" + uuid.uuid4().hex[:4])
    run_dir.mkdir(parents=True)
    fname = _java_file(code) if lid == "java" else rt["file"]
    f = run_dir / fname; f.write_text(code, encoding="utf-8")
    if lid == "csharp": (run_dir / "app.csproj").write_text(CSPROJ)
    bin_ = run_dir / ("main.exe" if IS_WIN else "main")
    sub = lambda tpl: [x.replace("{exe}", d["path"]).replace("{file}", str(f)).replace("{bin}", str(bin_)).replace("{dir}", str(run_dir)) for x in tpl]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1", GOCACHE=str(workspace() / ".gocache"))
    t0 = time.time()
    async def run(cmd, inp, tmo):
        p = await asyncio.create_subprocess_exec(*cmd, cwd=str(run_dir), env=env, stdin=asyncio.subprocess.PIPE,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **NOWIN)
        try: out, err = await asyncio.wait_for(p.communicate((inp or "").encode()), tmo)
        except asyncio.TimeoutError:
            try: p.kill()
            except Exception: pass
            return None, "", f"Timed out after {tmo}s (the program was stopped)."
        return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    try:
        if rt.get("compile"):
            rc, out, err = await run(sub(rt["compile"]), "", max(timeout, 60))
            if rc != 0:
                return {"ok": False, "stage": "compile", "exit_code": rc, "stdout": _clip(out), "stderr": _clip(err), "dir": str(run_dir), "file": str(f), "seconds": round(time.time() - t0, 2)}
        rc, out, err = await run(sub(rt["run"]) + [str(a) for a in (args or [])], stdin, timeout + (60 if lid in ("csharp", "go") else 0))
    except FileNotFoundError as e:
        return {"ok": False, "stage": "setup", "error": f"Could not start {rt['label']}: {e}"}
    return {"ok": rc == 0, "stage": "run", "exit_code": rc, "stdout": _clip(out), "stderr": _clip(err), "dir": str(run_dir), "file": str(f),
            "seconds": round(time.time() - t0, 2), "language": lid}

def format_result(r, attempt=None, max_attempts=3):
    if r.get("stage") == "setup": return "Error: " + r["error"]
    lines = [f"{'✅ Success' if r['ok'] else '❌ Failed'} ({r['stage']}, exit code {r.get('exit_code')}, {r.get('seconds')}s) - saved as {r.get('file')}"]
    if r.get("stdout"): lines.append("--- stdout ---\n" + r["stdout"].rstrip())
    if r.get("stderr"): lines.append("--- stderr ---\n" + r["stderr"].rstrip())
    if not r["ok"] and attempt is not None:
        if attempt < max_attempts:
            lines.append(f"\n[Attempt {attempt} of {max_attempts} failed. Read the error above, fix the code, and call run_code again with the COMPLETE corrected program.]")
        else:
            lines.append(f"\n[Attempt {attempt} of {max_attempts} failed. Stop retrying: tell the user what went wrong and what you tried.]")
    return "\n".join(lines)
