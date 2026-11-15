// Jiv 1.3: Memory, Skills, feedback (👍/👎 + corrections) and the Languages page
function fbBar(mdEl, convId, msgId, rating) {
  const body = mdEl.parentNode; if (body.querySelector(".fb")) return;
  const bar = document.createElement("div"); bar.className = "fb"; bar.dataset.msg = msgId; bar.dataset.cid = convId;
  bar.innerHTML = `<button class="icon" data-fb="1" title="Good answer">👍</button><button class="icon" data-fb="-1" title="Bad answer - tell Jiv what was wrong">👎</button><span class="fb-msg muted small"></span>`;
  body.append(bar); if (rating) markFb(bar, rating);
}
function markFb(bar, r) { bar.querySelectorAll("[data-fb]").forEach(b => b.classList.toggle("on", +b.dataset.fb === r)); }
async function sendFb(bar, rating, correction) {
  const r = await api("/api/feedback", { method: "POST", body: { conv_id: bar.dataset.cid, msg_id: +bar.dataset.msg, rating, correction: correction || "" } });
  markFb(bar, rating);
  bar.querySelector(".fb-msg").textContent = r.lesson_id ? "🧠 Saved as a lesson - Jiv will use it next time." : "Thanks!";
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-fb]"); if (!b) return;
  const bar = b.closest(".fb"), rating = +b.dataset.fb;
  if (rating > 0) return sendFb(bar, 1);
  if (bar.querySelector(".fb-fix")) return;
  const f = document.createElement("div"); f.className = "fb-fix";
  f.innerHTML = `<textarea class="f" rows="2" placeholder="What was wrong, or what is the right answer? (optional - this becomes a lesson)"></textarea><div class="hstack"><button class="primary" data-fbsend>Send feedback</button><button class="ghost" data-fbcancel>Cancel</button></div>`;
  bar.after(f); f.querySelector("textarea").focus();
  f.querySelector("[data-fbsend]").onclick = async () => { await sendFb(bar, -1, f.querySelector("textarea").value); f.remove(); };
  f.querySelector("[data-fbcancel]").onclick = () => f.remove();
});

function memoryNotice(ev) {
  const n = document.createElement("div"); n.className = "notice mem"; n.innerHTML = `🧠 ${ev.auto ? "Remembered" : "Saved to memory"}: ${esc(ev.text)} <button class="link" data-openmem>Manage</button>`;
  $("#messages").append(n);
}
function skillOffer(host, ev) {
  const d = document.createElement("div"); d.className = "skill-offer";
  d.innerHTML = `<h4>💡 That worked. Save it as a skill?</h4><div class="muted small">Jiv can reuse this ${esc(ev.language)} script later with <code>run_skill</code>.</div>
    <div class="row"><label>Name</label><input class="sk-name" value="${esc(ev.name || "")}"></div>
    <div class="row"><label>Description</label><input class="sk-desc" value="${esc(ev.description || "")}"></div>
    <details><summary class="muted small">Script (${esc(ev.language)}, ${ev.code.split("\n").length} lines)</summary><pre>${esc(ev.code)}</pre></details>
    <div class="hstack"><button class="primary" data-sksave>Save skill</button><button class="ghost" data-skno>No thanks</button><span class="sk-msg muted"></span></div>`;
  host.append(d);
  d.querySelector("[data-skno]").onclick = () => d.remove();
  d.querySelector("[data-sksave]").onclick = async () => {
    try {
      const s = await api("/api/skills", { method: "POST", body: { name: d.querySelector(".sk-name").value, description: d.querySelector(".sk-desc").value, steps: ev.task || "", language: ev.language, script: ev.code } });
      d.innerHTML = `<div class="muted">✅ Saved skill <b>${esc(s.name)}</b>. Ask "run my ${esc(s.name)} skill" any time, or see 🛠 Skills.</div>`;
    } catch (e) { d.querySelector(".sk-msg").textContent = e.message; }
  };
}
document.addEventListener("click", e => { if (e.target.closest("[data-openmem]")) openMemory(); });

async function openMemory(tab = "memory") {
  const d = await api("/api/memory");
  const facts = d.memories.filter(m => m.kind !== "lesson"), lessons = d.memories.filter(m => m.kind === "lesson");
  const rows = list => list.map(m => `<tr data-mid="${m.id}"><td><span class="tag ${m.kind}">${m.kind}</span></td><td class="mt">${esc(m.text)}</td><td class="muted small">${m.source}${m.uses ? " · used " + m.uses + "×" : ""}</td>
     <td class="nowrap"><button class="icon" data-medit title="Edit">✏️</button><button class="icon" data-mdel title="Delete">🗑</button></td></tr>`).join("");
  modal(`<div class="wiz-head"><h2>🧠 Memory</h2><button class="icon" data-close="1">✕</button></div>
   <div class="tabs"><button data-tab="memory" class="${tab === "memory" ? "on" : ""}">Facts & preferences (${facts.length})</button><button data-tab="lessons" class="${tab === "lessons" ? "on" : ""}">Lessons from feedback (${lessons.length})</button></div>
   <p class="muted small">Jiv saves facts and preferences you mention (e.g. "my name is…", "I prefer…") and lessons from 👎 corrections. Only the few most relevant are added to each prompt. Everything stays on this computer.</p>
   <div class="hstack"><input id="mNew" placeholder="${tab === "lessons" ? "Add a lesson, e.g. Always answer in British English" : "Add a fact, e.g. I use Windows 11 and VS Code"}"><button class="primary" id="mAdd">Add</button><button id="mMerge" title="Merge entries that say almost the same thing">🧹 Merge duplicates</button><span id="mMergeMsg" class="muted small"></span></div>
   <label class="hstack small" style="margin:8px 0"><input type="checkbox" id="mAuto" style="width:auto" ${d.auto_memory ? "checked" : ""}> Remember facts and preferences automatically</label>
   <table class="audit mem-table">${rows(tab === "lessons" ? lessons : facts) || `<tr><td class="muted">Nothing yet.</td></tr>`}</table>`);
  $("#modalCard").classList.add("wide");
  $("#mMerge").onclick = async () => { const r = await api("/api/memory/merge", { method: "POST", body: {} }); await openMemory(tab); $("#mMergeMsg").textContent = r.merged ? `Merged ${r.merged} duplicate${r.merged > 1 ? "s" : ""}.` : "No duplicates found."; };
  $("#modalCard").querySelectorAll("[data-tab]").forEach(b => b.onclick = () => openMemory(b.dataset.tab));
  $("#mAdd").onclick = async () => { const t = $("#mNew").value.trim(); if (!t) return; await api("/api/memory", { method: "POST", body: { text: t, kind: tab === "lessons" ? "lesson" : /\b(prefer|like|always|never|want)\b/i.test(t) ? "pref" : "fact" } }); openMemory(tab); };
  $("#mAuto").onchange = e => api("/api/memory/settings", { method: "POST", body: { auto_memory: e.target.checked } });
  $("#modalCard").querySelectorAll("[data-mdel]").forEach(b => b.onclick = async () => { await api("/api/memory/" + b.closest("tr").dataset.mid, { method: "DELETE" }); openMemory(tab); });
  $("#modalCard").querySelectorAll("[data-medit]").forEach(b => b.onclick = () => {
    const tr = b.closest("tr"), td = tr.querySelector(".mt"), old = td.textContent;
    td.innerHTML = `<div class="hstack"><input value="${esc(old)}"><button class="primary" data-msave>Save</button></div>`;
    td.querySelector("[data-msave]").onclick = async () => { await api("/api/memory/" + tr.dataset.mid, { method: "PUT", body: { text: td.querySelector("input").value } }); openMemory(tab); };
  });
}

async function openSkills() {
  const { skills } = await api("/api/skills");
  modal(`<div class="wiz-head"><h2>🛠 Skills</h2><button class="icon" data-close="1">✕</button></div>
   <p class="muted small">Skills are procedures and scripts that worked before. Jiv offers to save one after code runs successfully, finds relevant skills for new tasks, and runs them with <code>run_skill</code> (same permission as run_code).</p>
   <div id="skList">${skills.map(s => `<details class="skill" data-sid="${s.id}"><summary><b>${esc(s.name)}</b> <span class="badge">${esc(s.language || "steps")}</span> <span class="muted small">${esc(s.description)}</span> <span class="muted small">· used ${s.uses}×${s.last_ok ? "" : " · last run failed"}</span></summary>
     <div class="row"><label>Description</label><input class="sd" value="${esc(s.description)}"></div>
     <div class="row"><label>Steps</label><textarea class="f ss" rows="2">${esc(s.steps || "")}</textarea></div>
     ${s.script ? `<div class="row"><label>Script</label><textarea class="f sc mono" rows="8">${esc(s.script)}</textarea></div>` : ""}
     <div class="hstack"><button class="primary" data-sksv>Save changes</button><button data-skrun>▶ Ask Jiv to run it</button><button class="danger" data-skdel>Delete</button></div></details>`).join("") || '<div class="muted">No skills yet. Ask Jiv to write and run some code; when it works, click "Save skill".</div>'}</div>`);
  $("#modalCard").classList.add("wide");
  $("#modalCard").querySelectorAll(".skill").forEach(el => {
    const id = el.dataset.sid, name = el.querySelector("summary b").textContent;
    el.querySelector("[data-skdel]").onclick = async () => { if (confirm("Delete skill " + name + "?")) { await api("/api/skills/" + id, { method: "DELETE" }); openSkills(); } };
    el.querySelector("[data-sksv]").onclick = async () => { await api("/api/skills/" + id, { method: "PUT", body: { description: el.querySelector(".sd").value, steps: el.querySelector(".ss").value, ...(el.querySelector(".sc") ? { script: el.querySelector(".sc").value } : {}) } }); openSkills(); };
    el.querySelector("[data-skrun]").onclick = () => { closeModal(); $("#input").value = `Run my "${name}" skill.`; $("#input").focus(); };
  });
}

async function openLanguages(force) {
  modal(`<div class="wiz-head"><h2>{ } Languages</h2><button class="icon" data-close="1">✕</button></div><div class="center"><div class="spinner"></div><p class="muted">Looking for installed languages…</p></div>`);
  const d = await api("/api/languages" + (force ? "?force=1" : ""));
  const inst = d.installer === "winget" ? "winget" : d.installer === "brew" ? "Homebrew" : "";
  modal(`<div class="wiz-head"><h2>{ } Languages</h2><button class="icon" data-close="1">✕</button></div>
   <p class="muted small">Jiv's <code>run_code</code> tool writes programs to <code>${esc(d.workspace)}</code>, compiles and runs them with a time limit, and retries up to 3 times when there's an error. ${inst ? `Missing languages install with ${inst} after you approve.` : "Install missing languages with your system's package manager."}${inst && !d.installer_found ? ` <b>${inst} was not found on this computer.</b>` : ""}</p>
   <div id="lgPerm"></div>
   <table class="audit lang-table"><tr><th>Language</th><th>Status</th><th>Version / location</th><th></th></tr>
   ${d.runtimes.map(r => `<tr data-lid="${r.id}"><td><b>${esc(r.label)}</b></td><td>${r.installed ? `<span class="tag good">✓ installed</span>` : `<span class="tag">not installed</span>`}</td>
     <td class="small">${esc(r.version || "")}<div class="muted">${esc(r.installed ? r.path : (inst === "winget" ? "winget id " + r.winget : inst ? "brew " + r.brew : ""))}</div></td>
     <td class="nowrap">${!r.installed && r.can_install ? `<button class="primary" data-lginst>Install…</button>` : r.builtin ? `<button data-lginst ${r.can_install ? "" : "disabled"}>Install full Python…</button>` : ""}<div class="lg-log"></div></td></tr>`).join("")}</table>
   <div class="hstack" style="justify-content:flex-end;margin-top:8px"><button id="lgRefresh">🔄 Re-scan</button></div>`);
  $("#modalCard").classList.add("wide");
  $("#lgRefresh").onclick = () => openLanguages(true);
  $("#modalCard").querySelectorAll("[data-lginst]").forEach(b => b.onclick = async () => {
    const tr = b.closest("tr"), r = d.runtimes.find(x => x.id === tr.dataset.lid);
    const ok = await askPermission($("#lgPerm"), `Install ${r.label}`, r.install_cmd + `\n(downloads from the official package source; may show a Windows admin prompt)`, "install_language", { id: r.id });
    if (!ok) return;
    const log = tr.querySelector(".lg-log"); b.disabled = true; b.textContent = "Installing…";
    try {
      const { job } = await api("/api/languages/install", { method: "POST", body: { id: r.id, approved: true } });
      const poll = setInterval(async () => {
        const j = await api("/api/languages/jobs/" + job);
        log.innerHTML = `<pre class="mini-log">${esc(j.log.slice(-4).join("\n"))}</pre>`;
        if (j.status !== "running") { clearInterval(poll); b.textContent = j.status === "done" ? "✓ Done" : "Failed"; if (j.status === "done") setTimeout(() => openLanguages(true), 800); }
      }, 1000);
    } catch (e) { log.innerHTML = `<div class="err">${esc(e.message)}</div>`; b.disabled = false; b.textContent = "Install…"; }
  });
}
document.addEventListener("DOMContentLoaded", () => {
  $("#openMemory").onclick = () => openMemory(); $("#openSkills").onclick = () => openSkills(); $("#openLanguages").onclick = () => openLanguages();
});
