"""Platform-branch tests for tools (simulates macOS / Windows on any OS by patching)."""
import asyncio, os, sys, subprocess, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jiv import tools as T, agent as A
res = []
def check(n, c): res.append(c); print(("PASS " if c else "FAIL ") + n)
def as_os(win, mac, which):
    T.IS_WIN, T.IS_MAC = win, mac
    T.shutil.which = lambda x, _w=which: _w.get(x)
real_which, real_run = T.shutil.which, T.subprocess.run
calls = []
def fake_run(args, **kw):
    calls.append(args); return types.SimpleNamespace(returncode=0, stdout="clip text" if args == ["pbpaste"] else "", stderr="")
try:
    as_os(False, True, {"zsh": "/bin/zsh", "bash": "/bin/bash", "pbpaste": "/usr/bin/pbpaste", "open": "/usr/bin/open"})
    check("mac: default shell is zsh", T.default_shell() == "zsh" and T.shell_choices()[1] == "zsh")
    check("mac: permission card shows zsh", A.describe("run_shell", {"command": "ls ~"}) == "zsh > ls ~")
    check("mac: model-sent 'powershell' maps to zsh on card", A.describe("run_shell", {"command": "ls", "shell": "powershell"}) == "zsh > ls")
    T.subprocess.run = fake_run
    asyncio.run(T.open_target("Safari")); check("mac: open app by name uses open -a", calls[-1] == ["open", "-a", "Safari"])
    asyncio.run(T.open_target("https://example.com")); check("mac: URL uses open", calls[-1] == ["open", "https://example.com"])
    asyncio.run(T.open_target("~")); check("mac: path uses open <path>", calls[-1] == ["open", os.path.expanduser("~")])
    out = asyncio.run(T.read_clipboard()); check("mac: clipboard via pbpaste", out == "clip text" and calls[-1] == ["pbpaste"])
    as_os(True, False, {"powershell": "C:/ps.exe"})
    check("windows: default shell powershell + card", T.default_shell() == "powershell" and A.describe("run_shell", {"command": "dir"}) == "powershell > dir")
    check("windows: cmd choice kept", A.describe("run_shell", {"command": "dir", "shell": "cmd"}) == "cmd > dir")
    as_os(False, False, {"bash": "/bin/bash"})
    check("linux: bash", T.default_shell() == "bash" and A.describe("run_shell", {"command": "ls"}) == "bash > ls")
finally:
    T.subprocess.run = real_run; T.shutil.which = real_which; T.IS_WIN, T.IS_MAC = sys.platform == "win32", sys.platform == "darwin"
r = asyncio.run(T.run_shell("echo hi-from-shell")); check("real shell run on this box", "hi-from-shell" in r)
print(f"{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
