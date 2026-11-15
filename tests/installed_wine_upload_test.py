"""Wine: install JivSetup-1.2.0.exe, apply jiv-1.2.2-file-upload.jivupdate (pip installs pypdf/openpyxl from PyPI into the
embedded Python), then attach a PDF in headless Chrome and ask about it. python tests/installed_wine_upload_test.py <setup.exe> <pkg> [prefix]"""
import json, os, re, socket, subprocess, sys, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable
SETUP, PKG = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
PREFIX = Path(sys.argv[3] if len(sys.argv) > 3 else os.environ.get("WINEPREFIX", Path.home() / ".wine"))
USER = os.environ.get("USER", "box")
LOCAL = PREFIX / f"drive_c/users/{USER}/AppData/Local/Programs/JivDesktop"; DATA = PREFIX / f"drive_c/users/{USER}/AppData/Roaming/JivDesktop"
PORT, LLM = 18765, 18766; B = f"http://127.0.0.1:{PORT}"; OUT = SRC / "screenshots"; FX = SRC / "tests/fixtures/attach"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("JIV_")}; ENV.update(WINEPREFIX=str(PREFIX), WINEDEBUG="-all", JIV_HEALTH_TIMEOUT="60")
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:500]}]"), flush=True)
def wine(*a, wait=True):
    if wait: return subprocess.run(["wine", *map(str, a)], env=ENV, capture_output=True, text=True, timeout=300)
    return subprocess.Popen(["wine", *map(str, a)], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
def health(t=120, want=None):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            h = httpx.get(B + "/health", timeout=2).json()
            if not want or h["version"] == want: return h
        except Exception: pass
        time.sleep(1)
def client():
    tok = re.search(r'jiv-token" content="([^"]+)"', httpx.get(B + "/").text).group(1)
    return httpx.Client(base_url=B, headers={"X-Jiv-Token": tok}, timeout=600), tok
subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA"); time.sleep(3)
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)])
try:
    check("install 1.2.0", wine(SETUP, "/S").returncode == 0); time.sleep(2)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "config.json").write_text(json.dumps({"port": PORT, "setup_done": True, "base_url": f"http://127.0.0.1:{LLM}/v1", "model": "qwen2.5:3b"}))
    wine(LOCAL / "JivDesktop.exe", "--no-browser", wait=False)
    check("1.2.0 running", (health() or {}).get("version") == "1.2.0")
    c, tok = client()
    check("1.2.0 has no upload endpoint yet", c.post("/api/uploads", content=b"x").status_code in (404, 405))
    st = c.post("/api/updates/upload", content=PKG.read_bytes(), headers={"X-Filename": PKG.name}).json()
    check("1.2.2 package verified, signed, lists pip packages", st.get("ok") and st.get("signed") and st["manifest"].get("requirements") == ["pypdf>=4.0", "openpyxl>=3.1"], st)
    t0 = time.time(); a = c.post(f"/api/updates/apply/{st['id']}", json={"approved": True})
    check("apply (pip install from PyPI into embedded Python) → restarting", a.status_code == 200 and a.json().get("restarting"), a.text)
    print(f"     (pip + apply took {time.time() - t0:.0f}s)")
    h = health(180, "1.2.2"); check("restarted as 1.2.2 (installed layout)", h and h["version"] == "1.2.2", h)
    sp = LOCAL / "python/Lib/site-packages"
    check("pypdf + openpyxl in embedded site-packages", (sp / "pypdf").is_dir() and (sp / "openpyxl").is_dir() and (sp / "et_xmlfile").is_dir())
    time.sleep(2); c, tok = client()
    m = c.post("/api/uploads", content=(FX / "quarterly-report.pdf").read_bytes(), headers={"X-Filename": "quarterly-report.pdf"}).json()
    check("PDF extracted by the embedded Python", "Revenue grew 18%" in m.get("preview", ""), m)
    x = c.post("/api/uploads", content=(FX / "sales.xlsx").read_bytes(), headers={"X-Filename": "sales.xlsx"}).json()
    check("XLSX extracted (openpyxl)", "North,120" in x.get("preview", ""), x)
    check("stored in %APPDATA%\\JivDesktop\\uploads", (DATA / "uploads" / m["id"] / "quarterly-report.pdf").exists())
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1360, "height": 860}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); pg.wait_for_selector("#attachBtn")
        with pg.expect_file_chooser() as fc: pg.click("#attachBtn")
        fc.value.set_files([str(FX / "quarterly-report.pdf")]); pg.wait_for_selector("#attachChips .chip-att.ready")
        pg.fill("#input", "What are the key points of this report?")
        pg.screenshot(path=str(OUT / "32_installed_attach_pdf.png"))
        pg.click("#send"); pg.wait_for_selector(".msg.bot:has-text('it says')", timeout=60000); pg.wait_for_selector("#send:not(.hidden)")
        check("asking about the PDF works in the installed app", "Revenue grew 18%" in pg.inner_text("#messages"))
        pg.wait_for_timeout(300); pg.screenshot(path=str(OUT / "33_installed_ask_about_pdf.png"))
        check("export button from 1.2.1 included", pg.is_visible("#exportChat"))
        check("no page errors", not errs, errs); b.close()
    msgs_sys = None
finally:
    llm.terminate(); subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
    if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA")
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
