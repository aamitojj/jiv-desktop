"""Build + test an update that Jiv drafted itself (propose_update tool)."""
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from . import __version__
from . import updater as U

DEFAULT_TEST_CMD = "{python} tests/test_recommend.py && {python} tests/test_tools_platform.py"

def propose(title, changelog, files, deletes=(), version=None, test_cmd=None, run_tests=True):
    """files: [{"path":..., "content":...}] or {path: content}. Returns staged meta with checks."""
    if isinstance(files, list): files = {f["path"]: f.get("content", "") for f in files if isinstance(f, dict) and f.get("path")}
    files = dict(files or {}); deletes = list(deletes or [])
    if not files and not deletes: raise U.UpdateError("the update contains no changes")
    checks = []
    norm = {}
    for p, content in files.items():
        rp = U.safe_relpath(p); norm[rp] = content if isinstance(content, str) else str(content)
    deletes = [U.safe_relpath(p) for p in deletes]
    # 1) syntax check every Python file (compile only, nothing is executed)
    ok_syntax = True
    for rp, src in norm.items():
        if rp.endswith(".py"):
            try: compile(src, rp, "exec"); checks.append({"name": f"py_compile {rp}", "ok": True})
            except SyntaxError as e:
                ok_syntax = False; checks.append({"name": f"py_compile {rp}", "ok": False, "output": f"line {e.lineno}: {e.msg}"})
    version = version or U.bump_patch(__version__)
    if U.vtuple(version) <= U.vtuple(__version__): version = U.bump_patch(__version__)
    norm = U.with_version_file(norm, version, as_bytes=False)
    tmpd = Path(tempfile.mkdtemp(prefix="jiv-selfupdate-"))
    pkg = tmpd / f"jiv-{version}-local.jivupdate"
    U.build_package(pkg, version, changelog or title, files={k: v.encode("utf-8") for k, v in norm.items()}, deletes=deletes,
                    min_version=__version__, name=title or f"Jiv {version} (local)", source="self")
    # 2) run the test command in a throw-away copy of the app with the change applied
    if run_tests and ok_syntax:
        sandbox = tmpd / "app"
        for rel, full in U.iter_app_files():
            t = sandbox / rel; t.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(full, t)
        for rp, src in norm.items():
            t = sandbox / rp; t.parent.mkdir(parents=True, exist_ok=True); t.write_text(src, encoding="utf-8")
        for rp in deletes: (sandbox / rp).unlink(missing_ok=True)
        cmd = (test_cmd or DEFAULT_TEST_CMD).replace("{python}", f'"{sys.executable}"')
        env = dict(os.environ, JIV_DATA_DIR=str(tmpd / "data"), JIV_APP_DIR=str(sandbox), PYTHONDONTWRITEBYTECODE="1")
        t0 = time.time()
        try:
            r = subprocess.run(cmd, shell=True, cwd=sandbox, capture_output=True, text=True, timeout=300, env=env,
                               encoding="utf-8", errors="replace")
            out = (r.stdout + r.stderr)[-3000:]
            checks.append({"name": "tests: " + (test_cmd or DEFAULT_TEST_CMD).replace("{python}", "python"), "ok": r.returncode == 0, "output": out,
                           "seconds": round(time.time() - t0, 1)})
        except subprocess.TimeoutExpired:
            checks.append({"name": "tests", "ok": False, "output": "timed out after 300s"})
    elif not ok_syntax:
        checks.append({"name": "tests", "ok": False, "output": "skipped because of syntax errors"})
    meta = U.stage_file(pkg, origin="self", extra={"checks": checks, "checks_ok": all(c["ok"] for c in checks), "title": title})
    shutil.rmtree(tmpd, ignore_errors=True)
    return meta
