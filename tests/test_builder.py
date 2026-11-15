"""Jiv 1.6.x Builder: template expansion, path safety, error parsing, snippets,
template save/load, generate all builtin templates, compile/run Java when a JDK
is present, and package a website zip. No model or Ollama is used.
python tests/test_builder.py
"""
import json, os, re, shutil, socket, sys, tempfile, time, zipfile
from pathlib import Path
import httpx

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
TMP = Path(tempfile.mkdtemp(prefix="jiv-builder-"))
DATA = TMP / "data"
os.environ["JIV_DATA_DIR"] = str(DATA)
PY = sys.executable

from jiv import builder as B

res = []
def check(n, c, i=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:500]}]"), flush=True)

# ---- no AI on this path ----
src = (SRC / "jiv/builder.py").read_text()
imports = [ln for ln in src.splitlines() if ln.lstrip().startswith(("import ", "from "))]
check("builder.py imports nothing from ollama/providers (no model on this path)",
      all("ollama" not in ln.lower() and "providers" not in ln and "openai" not in ln.lower() for ln in imports)
      and "No model" in src[:500])

# ---- expansion ----
check("plain substitution", B.expand("Hi {{ name }}!", {"name": "Ada"}) == "Hi Ada!")
check("missing var is empty, not leftover braces", B.expand("x={{ missing }}.", {}) == "x=.")
check("if true / if false / else",
      B.expand("{% if on %}YES{% else %}NO{% endif %}", {"on": True}) == "YES"
      and B.expand("{% if on %}YES{% else %}NO{% endif %}", {"on": False}) == "NO")
check("nested if", B.expand("{% if a %}A{% if b %}B{% endif %}{% else %}C{% endif %}", {"a": 1, "b": 1}) == "AB")
pages = [{"name": "About", "file": "about.html"}]
out = B.expand("[{% for p in pages %}{{ p.name }}={{ p.file }};{% endfor %}]", {"pages": pages})
check("for-loop dotted fields", out == "[About=about.html;]", out)
try:
    B.expand("{% if x %}oops", {"x": 1}); check("unclosed if raises", False)
except B.BuilderError:
    check("unclosed if raises", True)

# ---- path safety ----
root = TMP / "proj"; root.mkdir()
try:
    B.safe_rel("../etc/passwd"); check("safe_rel blocks ..", False)
except B.BuilderError:
    check("safe_rel blocks ..", True)
try:
    B.safe_rel("C:/Windows/x"); check("safe_rel blocks drive-qualified rel", False)
except B.BuilderError:
    check("safe_rel blocks drive-qualified rel", True)
p = B.safe_join(root, "src/Main.java")
check("safe_join stays under root", p == (root / "src/Main.java").resolve() and str(p).startswith(str(root.resolve())))
try:
    # even if we somehow passed .. it must fail
    B.safe_join(root, "foo/../../etc/passwd"); check("safe_join blocks traversal", False)
except B.BuilderError:
    check("safe_join blocks traversal", True)
try:
    B.validate_folder("/etc/jiv-should-fail")
    check("validate_folder refuses /etc", False)
except B.BuilderError as e:
    check("validate_folder refuses /etc", "system" in str(e).lower() or "refusing" in str(e).lower(), e)
try:
    B.validate_folder(str(SRC / "jiv" / "evil"))
    check("validate_folder refuses the app folder", False)
except B.BuilderError as e:
    check("validate_folder refuses the app folder", "app folder" in str(e).lower(), e)
try:
    B.sanitize_name("../x"); check("sanitize_name rejects traversal-like names", False)
except B.BuilderError:
    check("sanitize_name rejects traversal-like names", True)

# ---- error parsing ----
err = "src/com/jiv/demo/Main.java:12: error: cannot find symbol\n  foo\nMain.java:3:5: warning: deprecated API\n"
got = B.parse_errors(err)
check("parse javac file:line error and file:line:col warning",
      len(got) == 2 and got[0]["file"].endswith("Main.java") and got[0]["line"] == 12
      and got[0]["kind"] == "error" and "symbol" in got[0]["msg"]
      and got[1]["line"] == 3 and got[1]["col"] == 5 and got[1]["kind"] == "warning", got)

# ---- context / templates present ----
ids = {t["id"] for t in B.list_templates()}
check("five builtin templates listed",
      {"static_website", "java_console", "java_swing", "java_api", "fullstack"} <= ids, ids)
sn = {s["id"] for s in B.list_snippets()}
check("builtin snippets include navbar, login, table, JsonStore",
      {"navbar", "login_form", "table", "JsonStore"} <= sn, sn)

# ---- generate each template ----
def gen(tid, name, opts):
    folder = TMP / "projects" / B.slugify(name)
    if folder.exists(): shutil.rmtree(folder)
    return B.generate(tid, name, opts, str(folder))

web = gen("static_website", "Demo Site", {"pages": "Home, About, Blog", "contact": True, "nav": True})
wroot = Path(web["folder"])
check("website wrote index, about, blog, contact, css, js",
      all((wroot / f).is_file() for f in ("index.html", "about.html", "blog.html", "contact.html", "css/style.css", "js/main.js"))
      and "page.html" not in web["files"], web["files"])
html = (wroot / "index.html").read_text()
check("website expanded name, nav links, no leftover tags",
      "Demo Site" in html and "about.html" in html and "contact.html" in html
      and "{%" not in html and "{{" not in html, html[:400])
web_off = gen("static_website", "Bare Site", {"pages": "Home", "contact": False, "nav": False})
check("contact page omitted when option is off",
      not (Path(web_off["folder"]) / "contact.html").exists()
      and "{% if" not in (Path(web_off["folder"]) / "index.html").read_text())

con = gen("java_console", "Hello Console", {"maven": True})
croot = Path(con["folder"])
main = next(croot.rglob("Main.java"))
check("console Main.java under package path and pom present",
      "com/jiv/hello_console/Main.java" in str(main).replace("\\", "/")
      and "package com.jiv.hello_console;" in main.read_text()
      and (croot / "pom.xml").is_file()
      and "{{" not in main.read_text())
con2 = gen("java_console", "No Maven App", {"maven": False})
check("pom omitted when maven is off", not (Path(con2["folder"]) / "pom.xml").exists())

sw = gen("java_swing", "Desk App", {"screens": 4})
sroot = Path(sw["folder"])
stxt = next(sroot.rglob("Main.java")).read_text()
check("swing has 4 screens and a menu, no template tags",
      stxt.count("Screen 4") >= 1 and "JMenu" in stxt and "cards.show" in stxt and "{%" not in stxt, stxt[400:700])

api_p = gen("java_api", "Items API", {"port": 18080})
aroot = Path(api_p["folder"])
atxt = next(aroot.rglob("Server.java")).read_text()
check("API uses JDK httpserver, JSON store, requested port, no Spring",
      "com.sun.net.httpserver" in atxt and "items.json" in atxt and "18080" in atxt
      and "springframework" not in atxt.lower() and "{%" not in atxt)

fs = gen("fullstack", "Full Stack Demo", {"port": 18081})
froot = Path(fs["folder"])
check("fullstack has frontend + Java server",
      (froot / "frontend/index.html").is_file() and (froot / "frontend/app.js").is_file()
      and next(froot.rglob("Server.java")).is_file()
      and "18081" in (froot / "frontend/app.js").read_text())

# ---- snippets ----
idx = wroot / "index.html"
before = idx.read_text()
r = B.insert_snippet(web["id"], "index.html", "navbar")
check("snippet inserted once before </body>", r["inserted"] is True and "snippet:navbar" in idx.read_text()
      and idx.read_text().index("snippet:navbar") < idx.read_text().lower().index("</body>"))
r2 = B.insert_snippet(web["id"], "index.html", "navbar")
check("second insert of same snippet is skipped", r2["inserted"] is False)
jr = B.insert_snippet(con["id"], "src/com/jiv/hello_console/Main.java", "JsonStore")
jtxt = (croot / "src/com/jiv/hello_console/Main.java").read_text()
jsib = croot / "src/com/jiv/hello_console/JsonStore.java"
check("Java class snippet is written as a sibling .java file (not inside Main)",
      jr["inserted"] is True and jsib.is_file() and "class JsonStore" in jsib.read_text()
      and "class JsonStore" not in jtxt)

# ---- save / load user template ----
saved = B.save_as_template(web["id"], "my_site_tpl", "My Site Template", "from test")
check("save_as_template writes under data/templates", saved["id"] == "my_site_tpl" and saved["files"] >= 3
      and (DATA / "templates" / "my_site_tpl" / "manifest.json").is_file())
again = {t["id"] for t in B.list_templates()}
check("user template appears in the list", "my_site_tpl" in again)
cloned = B.generate("my_site_tpl", "Cloned Site", {}, str(TMP / "projects" / "cloned_site"))
check("user template can generate a project", (Path(cloned["folder"]) / "index.html").is_file())

# ---- package website zip ----
job = B.package_project(web["id"], "zip")
t0 = time.time()
while time.time() - t0 < 20:
    j = B.job_view(job["id"])
    if j and j["status"] != "running": break
    time.sleep(0.05)
j = B.job_view(job["id"])
zpath = wroot / "dist" / (web["slug"] + "-site.zip")
check("website package job ok and zip exists", j and j["status"] == "ok" and zpath.is_file(), j)
if zpath.is_file():
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
    check("zip is Netlify-ready (index.html at root, no out/target)",
          "index.html" in names and "css/style.css" in names
          and not any(n.startswith(("out/", "target/", "dist/")) for n in names), names)
else:
    check("zip is Netlify-ready (index.html at root, no out/target)", False)

# ---- local static server ----
site = B.start_site(web["id"])
try:
    r = httpx.get(site["url"], timeout=5)
    check("local static server serves index on 127.0.0.1", r.status_code == 200 and "Demo Site" in r.text, r.status_code)
finally:
    B.stop_site(web["id"])

# ---- Java compile / run when a JDK is available ----
tools = B.detect_tools()
check("detect_tools returns expected keys",
      all(k in tools for k in ("java", "javac", "jar", "mvn", "gradle", "jpackage", "has_jdk")))
JDK = tools["has_jdk"] and tools["java"]
if not JDK:
    check("Java compile/run SKIPPED — no javac/java on this machine", True, tools)
else:
    def wait(job, t=60):
        t0 = time.time()
        while time.time() - t0 < t:
            j = B.job_view(job["id"])
            if j and j["status"] != "running": return j
            time.sleep(0.1)
        return B.job_view(job["id"])
    jb = wait(B.build_project(con["id"]))
    check("javac build of console app succeeds", jb and jb["status"] == "ok" and jb["code"] == 0, jb and jb.get("log"))
    # run: console prints and exits
    jr = wait(B.run_project(con["id"], timeout=20), t=25)
    log = "\n".join((jr or {}).get("log") or [])
    check("run console app prints greeting and exits 0",
          jr and jr.get("code") == 0 and "Hello from Hello Console" in log, log[-500:])
    jp = wait(B.package_project(con["id"], "jar"))
    jar = croot / "dist" / (con["slug"] + ".jar")
    check("package console as runnable jar", jp and jp["status"] == "ok" and jar.is_file(), (jp or {}).get("log"))
    if jar.is_file() and tools["java"]:
        import subprocess
        r = subprocess.run([tools["java"], "-jar", str(jar)], cwd=str(croot), capture_output=True, text=True, timeout=15)
        check("java -jar of packaged console works", r.returncode == 0 and "Hello from Hello Console" in (r.stdout + r.stderr), r.stdout + r.stderr)

    jb2 = wait(B.build_project(sw["id"]))
    check("javac build of Swing app succeeds (run skipped — needs a display)",
          jb2 and jb2["status"] == "ok", jb2 and jb2.get("log"))

    # pick a free port in case 18080 is taken — already baked into source; use generated port
    jb3 = wait(B.build_project(api_p["id"]))
    check("javac build of REST API succeeds", jb3 and jb3["status"] == "ok", jb3 and jb3.get("log"))
    # rewrite port? Server already has 18080. If bind fails we'll report.
    runj = B.run_project(api_p["id"])
    ok_h = False; body = ""
    t0 = time.time()
    while time.time() - t0 < 15:
        try:
            h = httpx.get("http://127.0.0.1:18080/health", timeout=1)
            if h.status_code == 200:
                ok_h = True; body = h.text; break
        except Exception:
            time.sleep(0.2)
    # also POST an item
    post_ok = False
    if ok_h:
        p = httpx.post("http://127.0.0.1:18080/items", content=b'{"name":"alpha"}', headers={"Content-Type": "application/json"}, timeout=3)
        g = httpx.get("http://127.0.0.1:18080/items", timeout=3)
        post_ok = p.status_code == 201 and "alpha" in g.text
    B.stop_run(runj["id"]); time.sleep(0.3)
    check("run API and GET /health", ok_h and "ok" in body.lower(), body)
    check("POST /items persists in JSON store", post_ok)

    jb4 = wait(B.build_project(fs["id"]))
    check("javac build of fullstack backend succeeds", jb4 and jb4["status"] == "ok", jb4 and jb4.get("log"))
    jz = wait(B.package_project(fs["id"], "zip"))
    fzip = froot / "dist" / (fs["slug"] + "-site.zip")
    check("fullstack frontend packages as zip", jz and jz["status"] == "ok" and fzip.is_file())
    if fzip.is_file():
        with zipfile.ZipFile(fzip) as z:
            check("fullstack zip has frontend index.html at root", "index.html" in z.namelist(), z.namelist())

    # error highlighting: break a file and rebuild
    broken = croot / "src/com/jiv/hello_console/Main.java"
    broken.write_text(broken.read_text().replace("String[] args", "String[] args, nope"))
    jb5 = wait(B.build_project(con["id"]))
    check("build errors are parsed with file and line",
          jb5 and jb5["status"] == "error" and jb5["errors"]
          and any(e["line"] > 0 and e["kind"] == "error" for e in jb5["errors"]), jb5.get("errors") if jb5 else jb5)

# ---- HTTP API: permissions, audit, no model required ----
def fp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
import subprocess
PORT = fp()
app = subprocess.Popen([PY, str(SRC / "run.py"), "--no-browser", "--port", str(PORT)],
                       cwd=str(SRC), env=dict(os.environ, JIV_DATA_DIR=str(DATA / "http")),
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
BURL = f"http://127.0.0.1:{PORT}"
try:
    tok = None
    for _ in range(80):
        try:
            tok = re.search(r'jiv-token" content="([^"]+)"', httpx.get(BURL + "/", timeout=1).text).group(1)
            break
        except Exception:
            time.sleep(0.25)
    check("app starts for Builder API without a model / Ollama", bool(tok) and httpx.get(BURL + "/health", timeout=2).json().get("version") == "1.6.2")
    if tok:
        c = httpx.Client(base_url=BURL, headers={"X-Jiv-Token": tok}, timeout=30)
        tpls = c.get("/api/builder/templates").json()["templates"]
        check("GET /api/builder/templates (no permission needed)", len(tpls) >= 5)
        folder = str(TMP / "http-proj" / "web1")
        body = {"template": "static_website", "name": "Http Site", "folder": folder,
                "options": {"pages": "Home, About", "contact": False, "nav": True}}
        r = c.post("/api/builder/generate", json=body)
        check("generate refused without approval", r.status_code == 400 and "permission" in r.text.lower(), r.text)
        r = c.post("/api/builder/generate", json={**body, "approved": True})
        check("generate with approval writes files", r.status_code == 200 and (Path(folder) / "index.html").is_file(), r.text[:400])
        audit = c.get("/api/audit").json()
        check("audit log records Builder generate",
              any((a.get("agent") or a.get("actor")) == "Builder" and a.get("tool") == "builder_generate" for a in audit), audit[:3] if isinstance(audit, list) else audit)
        bad = c.post("/api/builder/generate", json={"template": "static_website", "name": "X",
                     "folder": str(TMP / "http-proj" / "esc"), "options": {}, "approved": True})
        # write traversal via file API
        pid = r.json()["id"]
        wr = c.post(f"/api/builder/projects/{pid}/file",
                    json={"path": "../outside.txt", "content": "nope", "approved": True})
        check("API write with .. is rejected", wr.status_code == 400 and not (Path(folder).parent / "outside.txt").exists(), wr.text)
        # UI assets present
        page = httpx.get(BURL + "/", timeout=5).text
        check("sidebar includes Builder and builder.js is served",
              "openBuilder" in page and "builder.js" in page
              and "openBuilder" in httpx.get(BURL + "/static/builder.js", timeout=5).text)
finally:
    app.terminate()
    try: app.wait(timeout=8)
    except Exception: app.kill()

print(f"\n{sum(res)}/{len(res)} passed")
# cleanup
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if all(res) else 1)
