// Jiv 1.6.0 Builder — deterministic templates, no AI, no model calls.
const BD = { tab: "new", templates: [], snippets: [], projects: [], tools: {},
  sel: null, project: null, file: null, dirty: false, poll: null, job: null, site: null };

async function openBuilder(tab) {
  clearInterval(BD.poll);
  if (tab) BD.tab = tab;
  modal(`<div class="wiz-head"><h2>🧱 Builder</h2><button class="icon" data-close="1">✕</button></div>
    <p class="muted small">Create a website or Java app from a local template. Files are written with plain substitution — no AI, and it works with Ollama stopped. Writes, builds, runs and packages stay inside the project folder and always ask permission first (unless you turned on auto-approve).</p>
    <div class="tabs" id="bdTabs">
      <button data-bdtab="new">＋ New project</button>
      <button data-bdtab="list">📂 My projects</button>
      <button data-bdtab="edit">✎ Editor</button>
    </div>
    <div id="bdPerm"></div>
    <div id="bdBody"><div class="muted">Loading…</div></div>`);
  $("#modalCard").classList.add("wide", "builder");
  $("#bdTabs").querySelectorAll("[data-bdtab]").forEach(b => b.onclick = () => { BD.tab = b.dataset.bdtab; renderBuilder(); });
  try {
    const [t, s, p, tools] = await Promise.all([
      api("/api/builder/templates"), api("/api/builder/snippets"),
      api("/api/builder/projects"), api("/api/builder/tools")
    ]);
    BD.templates = t.templates; BD.snippets = s.snippets; BD.projects = p.projects; BD.tools = tools;
  } catch (e) { $("#bdBody").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  renderBuilder();
}

function renderBuilder() {
  $("#bdTabs").querySelectorAll("[data-bdtab]").forEach(b => b.classList.toggle("on", b.dataset.bdtab === BD.tab));
  if (BD.tab === "new") return renderNewProject();
  if (BD.tab === "list") return renderProjectList();
  return renderEditor();
}

function optFields(tpl) {
  return (tpl.options || []).map(o => {
    const id = "bdopt-" + o.id;
    if (o.type === "checkbox")
      return `<label class="bd-opt"><input type="checkbox" id="${id}" ${o.default ? "checked" : ""}> ${esc(o.label)}</label>`;
    if (o.type === "number")
      return `<div class="row"><label>${esc(o.label)}</label><input type="number" id="${id}" value="${esc(String(o.default ?? 0))}" min="${o.min ?? 0}" max="${o.max ?? 65535}"></div>`;
    return `<div class="row"><label>${esc(o.label)}</label><input type="text" id="${id}" value="${esc(String(o.default ?? ""))}"></div>`;
  }).join("");
}
function readOpts(tpl) {
  const o = {};
  (tpl.options || []).forEach(opt => {
    const el = document.getElementById("bdopt-" + opt.id); if (!el) return;
    o[opt.id] = opt.type === "checkbox" ? el.checked : (opt.type === "number" ? Number(el.value) : el.value);
  });
  return o;
}

function renderNewProject() {
  const tpls = BD.templates;
  if (!BD.sel) BD.sel = tpls[0] && tpls[0].id;
  const tpl = tpls.find(t => t.id === BD.sel) || tpls[0];
  $("#bdBody").innerHTML = `
    <div class="bd-tpls">${tpls.map(t => `<button class="bd-tpl ${t.id === BD.sel ? "on" : ""}" data-tid="${esc(t.id)}">
      <b>${esc(t.name)}</b><span class="muted small">${esc(t.kind || "")}${t.builtin ? "" : " · yours"}</span>
      <div class="muted small">${esc(t.description || "")}</div></button>`).join("") || "<div class='muted'>No templates found.</div>"}</div>
    ${tpl ? `<div class="bd-form">
      <div class="row"><label>Project name</label><input id="bdName" value="My App" maxlength="60"></div>
      <div class="row"><label>Folder</label><input id="bdFolder" placeholder="project folder"></div>
      ${optFields(tpl)}
      <div id="bdPreview" class="muted small"></div>
      <div class="hstack wiz-nav"><button id="bdPrevBtn">Preview files</button><button class="primary" id="bdCreate">Create project…</button></div>
    </div>` : ""}`;
  $("#bdBody").querySelectorAll("[data-tid]").forEach(b => b.onclick = () => { BD.sel = b.dataset.tid; renderNewProject(); });
  const name = $("#bdName"), folder = $("#bdFolder");
  async function fillFolder() {
    if (!name) return;
    try { const r = await api("/api/builder/default-folder?name=" + encodeURIComponent(name.value || "project")); if (folder && (!folder.dataset.touched || !folder.value)) folder.value = r.folder; } catch {}
  }
  if (folder) folder.addEventListener("input", () => folder.dataset.touched = "1");
  if (name) { name.addEventListener("change", fillFolder); fillFolder(); }
  if ($("#bdPrevBtn")) $("#bdPrevBtn").onclick = previewNew;
  if ($("#bdCreate")) $("#bdCreate").onclick = createNew;
}

async function previewNew() {
  const tpl = BD.templates.find(t => t.id === BD.sel);
  try {
    const r = await api("/api/builder/preview", { method: "POST", body: { template: tpl.id, name: $("#bdName").value, folder: $("#bdFolder").value, options: readOpts(tpl) } });
    $("#bdPreview").innerHTML = `<b>${r.count} files</b> in <code>${esc(r.folder)}</code><ul class="bd-files">${r.files.map(f => `<li><code>${esc(f.path)}</code></li>`).join("")}</ul>`;
    return r;
  } catch (e) { $("#bdPreview").innerHTML = `<div class="err">${esc(e.message)}</div>`; return null; }
}

async function createNew() {
  const tpl = BD.templates.find(t => t.id === BD.sel);
  const name = $("#bdName").value, folder = $("#bdFolder").value, options = readOpts(tpl);
  const prev = await previewNew(); if (!prev) return;
  const exact = `Create project "${name}" from template "${tpl.name}" in:\n${folder}\n\nFiles (${prev.count}):\n` + prev.files.map(f => "  " + f.path).join("\n");
  const ok = await askPermission($("#bdPerm"), "Create project", exact, "builder_generate", { template: tpl.id, folder });
  if (!ok) return;
  try {
    const proj = await api("/api/builder/generate", { method: "POST", body: { template: tpl.id, name, folder, options, approved: true } });
    BD.project = proj; BD.file = null; BD.dirty = false;
    const { projects } = await api("/api/builder/projects"); BD.projects = projects;
    BD.tab = "edit"; renderBuilder();
  } catch (e) { $("#bdPreview").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

function renderProjectList() {
  const rows = BD.projects || [];
  $("#bdBody").innerHTML = rows.length ? `<table class="audit"><tr><th>Name</th><th>Template</th><th>Folder</th><th></th></tr>
    ${rows.map(p => `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.template)}</td><td class="small"><code>${esc(p.folder)}</code></td>
      <td class="nowrap"><button class="primary" data-open="${p.id}">Open</button> <button class="ghost" data-forget="${p.id}" title="Remove from this list (files are kept)">Forget</button></td></tr>`).join("")}</table>`
    : `<div class="muted">No projects yet. Create one from a template.</div>`;
  $("#bdBody").querySelectorAll("[data-open]").forEach(b => b.onclick = async () => {
    BD.project = BD.projects.find(p => p.id === b.dataset.open); BD.file = null; BD.dirty = false; BD.tab = "edit"; renderBuilder();
  });
  $("#bdBody").querySelectorAll("[data-forget]").forEach(b => b.onclick = async () => {
    await api("/api/builder/projects/" + b.dataset.forget, { method: "DELETE" });
    BD.projects = (await api("/api/builder/projects")).projects;
    if (BD.project && BD.project.id === b.dataset.forget) BD.project = null;
    renderProjectList();
  });
}

async function renderEditor() {
  if (!BD.project) {
    $("#bdBody").innerHTML = `<div class="muted">Open or create a project first.</div>`;
    return;
  }
  let tree = [];
  try { tree = (await api("/api/builder/projects/" + BD.project.id + "/tree")).tree; }
  catch (e) { $("#bdBody").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const tools = BD.tools || {};
  const jdkNote = tools.has_jdk ? `<span class="tag good">JDK found</span>` :
    `<span class="tag">No JDK (javac) found</span> <button id="bdLang">Install a JDK on the Languages page…</button>`;
  const snippets = `<select id="bdSnip"><option value="">Insert a snippet…</option>${(BD.snippets || []).map(s => `<option value="${esc(s.id)}">${esc(s.name)} — ${esc(s.description || s.kind)}</option>`).join("")}</select>`;
  $("#bdBody").innerHTML = `
    <div class="bd-edit">
      <div class="bd-side">
        <div class="bd-proj"><b>${esc(BD.project.name)}</b><div class="muted small"><code>${esc(BD.project.folder)}</code></div></div>
        <div class="bd-tree">${tree.map(n => n.kind === "dir"
          ? `<div class="bd-dir">📁 ${esc(n.path)}</div>`
          : `<button class="bd-file ${BD.file === n.path ? "on" : ""}" data-path="${esc(n.path)}">📄 ${esc(n.path)}</button>`).join("") || "<div class='muted small'>Empty folder</div>"}</div>
      </div>
      <div class="bd-main">
        <div class="hstack bd-toolbar">
          <button class="primary" id="bdSave" ${BD.file ? "" : "disabled"}>Save</button>
          ${snippets}
          <button id="bdBuild">Build</button>
          <button id="bdRun">Run</button>
          <button id="bdStop" ${BD.job ? "" : "disabled"}>Stop</button>
          <button id="bdPkg">Package…</button>
          <button id="bdSite">Open site</button>
          <button id="bdAsTpl">Save as template…</button>
        </div>
        <div class="muted small hstack" id="bdTools">${jdkNote} ${tools.mvn ? "<span class='tag'>Maven</span>" : ""} ${tools.gradle ? "<span class='tag'>Gradle</span>" : ""} ${tools.jpackage ? "<span class='tag'>jpackage</span>" : ""}</div>
        <div id="bdErrs"></div>
        <textarea id="bdCode" spellcheck="false" ${BD.file ? "" : "disabled"} placeholder="Select a file in the tree"></textarea>
        <pre id="bdLog" class="bd-log"></pre>
      </div>
    </div>`;
  if ($("#bdLang")) $("#bdLang").onclick = () => openLanguages();
  $("#bdBody").querySelectorAll("[data-path]").forEach(b => b.onclick = () => openBdFile(b.dataset.path));
  $("#bdSave").onclick = saveBdFile;
  $("#bdBuild").onclick = () => bdJob("build");
  $("#bdRun").onclick = () => bdJob("run");
  $("#bdStop").onclick = stopBdJob;
  $("#bdPkg").onclick = packageBd;
  $("#bdSite").onclick = openBdSite;
  $("#bdAsTpl").onclick = saveBdTemplate;
  $("#bdSnip").onchange = insertBdSnippet;
  $("#bdCode").addEventListener("input", () => { BD.dirty = true; });
  if (BD.file) await loadBdFile(BD.file);
}

async function openBdFile(path) {
  if (BD.dirty && !confirm("Discard unsaved changes?")) return;
  await loadBdFile(path);
  BD.tab = "edit"; renderEditor();
}
async function loadBdFile(path) {
  const r = await api("/api/builder/projects/" + BD.project.id + "/file?path=" + encodeURIComponent(path));
  BD.file = path; BD.dirty = false;
  const ta = $("#bdCode");
  if (ta) { ta.value = r.content; ta.disabled = false; }
}
async function saveBdFile() {
  if (!BD.file) return;
  const exact = `Write file inside the project folder:\n${BD.project.folder}\n${BD.file}`;
  const ok = await askPermission($("#bdPerm"), "Save file", exact, "builder_write", { project: BD.project.id, path: BD.file });
  if (!ok) return;
  try {
    await api("/api/builder/projects/" + BD.project.id + "/file", { method: "POST", body: { path: BD.file, content: $("#bdCode").value, approved: true } });
    BD.dirty = false;
    $("#bdLog").textContent = "Saved " + BD.file;
  } catch (e) { $("#bdLog").textContent = e.message; }
}
async function insertBdSnippet() {
  const sid = $("#bdSnip").value; if (!sid || !BD.file) { $("#bdSnip").value = ""; return; }
  const exact = `Insert snippet "${sid}" into ${BD.file} in:\n${BD.project.folder}`;
  const ok = await askPermission($("#bdPerm"), "Insert snippet", exact, "builder_snippet", { snippet: sid, path: BD.file });
  $("#bdSnip").value = "";
  if (!ok) return;
  try {
    await api("/api/builder/projects/" + BD.project.id + "/snippet", { method: "POST", body: { path: BD.file, snippet: sid, approved: true } });
    await loadBdFile(BD.file);
    const ta = $("#bdCode"); if (ta) ta.value = (await api("/api/builder/projects/" + BD.project.id + "/file?path=" + encodeURIComponent(BD.file))).content;
    BD.dirty = false;
  } catch (e) { $("#bdLog").textContent = e.message; }
}
async function bdJob(kind) {
  const titles = { build: "Build project", run: "Run project" };
  const exact = (kind === "build"
    ? `Compile this project in:\n${BD.project.folder}\n(javac, or Maven/Gradle if a build file is present and the tool is installed).`
    : `Run this project's main class or jar in:\n${BD.project.folder}`);
  const ok = await askPermission($("#bdPerm"), titles[kind], exact, "builder_" + kind, { project: BD.project.id, folder: BD.project.folder });
  if (!ok) return;
  try {
    const j = await api("/api/builder/projects/" + BD.project.id + "/" + kind, { method: "POST", body: { approved: true } });
    BD.job = j.id; pollBdJob(j.id);
  } catch (e) { $("#bdLog").textContent = e.message; }
}
async function stopBdJob() {
  if (!BD.job) return;
  await api("/api/builder/projects/" + BD.project.id + "/stop", { method: "POST", body: { job: BD.job } });
}
async function pollBdJob(jid) {
  clearInterval(BD.poll);
  const tick = async () => {
    let j; try { j = await api("/api/builder/jobs/" + jid); } catch { return; }
    const log = $("#bdLog"); if (log) log.textContent = (j.log || []).join("\n");
    const box = $("#bdErrs");
    if (box) {
      box.innerHTML = (j.errors || []).map(e => `<button class="bd-err" data-ef="${esc(e.file)}" data-el="${e.line}">${esc(e.kind)} ${esc(e.file)}:${e.line}: ${esc(e.msg)}</button>`).join("");
      box.querySelectorAll(".bd-err").forEach(b => b.onclick = () => jumpBdErr(b.dataset.ef, Number(b.dataset.el)));
    }
    if (j.status !== "running") { clearInterval(BD.poll); if ($("#bdStop")) $("#bdStop").disabled = true; BD.job = j.status === "running" ? jid : null; }
    else if ($("#bdStop")) $("#bdStop").disabled = false;
  };
  BD.poll = setInterval(tick, 400); tick();
}
async function jumpBdErr(file, line) {
  let rel = file.replace(/\\/g, "/");
  const folder = (BD.project.folder || "").replace(/\\/g, "/");
  if (rel.startsWith(folder + "/")) rel = rel.slice(folder.length + 1);
  try { await openBdFile(rel); } catch { return; }
  const ta = $("#bdCode"); if (!ta) return;
  const lines = ta.value.split("\n");
  let pos = 0; for (let i = 0; i < Math.max(0, line - 1) && i < lines.length; i++) pos += lines[i].length + 1;
  ta.focus(); ta.setSelectionRange(pos, pos + (lines[line - 1] || "").length);
  const lineH = ta.scrollHeight / Math.max(1, lines.length);
  ta.scrollTop = Math.max(0, (line - 3) * lineH);
  ta.classList.add("bd-hl"); setTimeout(() => ta.classList.remove("bd-hl"), 1200);
}
async function packageBd() {
  const kind = prompt("Package as: zip (Netlify-ready site), jar (runnable Java), exe (jpackage if the JDK has it), or auto", "auto");
  if (!kind) return;
  const k = kind.trim().toLowerCase();
  if (!["auto", "zip", "jar", "exe"].includes(k)) { $("#bdLog").textContent = "Use auto, zip, jar or exe."; return; }
  const exact = `Package this project as ${k} in:\n${BD.project.folder}\n(output goes in the project's dist/ folder)`;
  const ok = await askPermission($("#bdPerm"), "Package project", exact, "builder_package", { project: BD.project.id, kind: k });
  if (!ok) return;
  try {
    const j = await api("/api/builder/projects/" + BD.project.id + "/package", { method: "POST", body: { kind: k, approved: true } });
    BD.job = j.id; pollBdJob(j.id);
  } catch (e) { $("#bdLog").textContent = e.message; }
}
async function openBdSite() {
  const exact = `Start a local static file server bound to 127.0.0.1 (a random free port) for:\n${BD.project.folder}`;
  const ok = await askPermission($("#bdPerm"), "Open site locally", exact, "builder_serve", { project: BD.project.id, bind: "127.0.0.1" });
  if (!ok) return;
  try {
    const r = await api("/api/builder/projects/" + BD.project.id + "/serve", { method: "POST", body: { approved: true } });
    BD.site = r; $("#bdLog").textContent = "Serving " + r.root + " at " + r.url;
    window.open(r.url, "_blank");
  } catch (e) { $("#bdLog").textContent = e.message; }
}
async function saveBdTemplate() {
  const name = prompt("Save this project as a user template. Template name:", BD.project.name + " template");
  if (!name) return;
  const exact = `Copy this project's source files into your user templates folder as "${name}" (skips build output).`;
  const ok = await askPermission($("#bdPerm"), "Save project as template", exact, "builder_save_template", { name });
  if (!ok) return;
  try {
    const r = await api("/api/builder/projects/" + BD.project.id + "/save-template", { method: "POST", body: { name, id: name, approved: true } });
    $("#bdLog").textContent = "Saved user template " + r.id + " (" + r.files + " files). It will appear under New project.";
    BD.templates = (await api("/api/builder/templates")).templates;
  } catch (e) { $("#bdLog").textContent = e.message; }
}

document.addEventListener("DOMContentLoaded", () => {
  const b = document.getElementById("openBuilder");
  if (b) b.onclick = () => openBuilder();
});
