// Jiv 1.4: Batch page - repeat a prompt N times, or run it over every item of a file, as a resumable background job.
const BS = { mode: "file", source: "", sourceLabel: "", templates: [], preview: null, estimate: null, poll: null, open: null, logFor: null, pvTimer: null };
const fmtDur = s => { if (s == null || !isFinite(s)) return "–"; s = Math.round(s); if (s < 60) return s + " s";
  const m = Math.round(s / 60); if (m < 60) return m + " min"; const h = Math.floor(m / 60); return h < 48 ? `${h} h ${m % 60} min` : `${(h / 24).toFixed(1)} days`; };
const dl = (jid, f) => `/api/batch/jobs/${jid}/download/${encodeURIComponent(f)}?t=${encodeURIComponent(TOKEN)}`;

async function openBatch(prefill = {}) {
  clearInterval(BS.poll);
  const { templates } = await api("/api/batch/templates"); BS.templates = templates;
  BS.mode = prefill.mode || (prefill.tab === "jobs" ? "jobs" : BS.mode === "jobs" ? "file" : BS.mode);
  if (prefill.tab === "jobs") BS.mode = "jobs";
  modal(`<div class="wiz-head"><h2>⚡ Batch</h2><button class="icon" data-close="1">✕</button></div>
   <p class="muted small">Process big jobs piece by piece with your local model: each item is sent on its own, so it fits a small model's context. Jobs run in the background, survive a restart, and can be paused, resumed or cancelled.</p>
   <div class="tabs" id="bTabs"><button data-bmode="repeat">🔁 Repeat N times</button><button data-bmode="file">📄 Run over a file</button><button data-bmode="jobs">📋 Jobs <span id="bJobCount"></span></button></div>
   <div id="bBody"></div>`);
  $("#modalCard").classList.add("wide", "batch");
  $("#bTabs").querySelectorAll("[data-bmode]").forEach(b => b.onclick = () => { BS.mode = b.dataset.bmode; renderBatch(); });
  renderBatch(prefill);
  BS.poll = setInterval(() => { if ($("#modal").classList.contains("hidden") || !$("#bBody")) { clearInterval(BS.poll); return; } if (BS.mode === "jobs") refreshJobs(); else jobCount(); }, 1500);
  jobCount();
}
async function jobCount() { try { const { jobs } = await api("/api/batch/jobs"); const a = jobs.filter(j => ["running", "queued", "paused"].includes(j.status)).length; if ($("#bJobCount")) $("#bJobCount").textContent = jobs.length ? `(${a ? a + " active" : jobs.length})` : ""; } catch { } }

function renderBatch(prefill = {}) {
  $("#bTabs").querySelectorAll("[data-bmode]").forEach(b => b.classList.toggle("on", b.dataset.bmode === BS.mode));
  if (BS.mode === "jobs") { $("#bBody").innerHTML = `<div id="bJobs"><div class="muted">Loading…</div></div>`; return refreshJobs(); }
  const rep = BS.mode === "repeat";
  const tpls = BS.templates.filter(t => (t.mode === "repeat") === rep);
  $("#bBody").innerHTML = `
   <div class="row"><label>Template</label><div class="hstack"><select id="bTpl"><option value="">— custom prompt —</option>${tpls.map(t => `<option value="${t.id}">${t.builtin ? "★ " : ""}${esc(t.name)}</option>`).join("")}</select>
     <button id="bTplDel" class="ghost hidden" title="Delete this saved template">🗑</button></div></div>
   <div class="muted small" id="bTplDesc"></div>
   <div class="row"><label>Speed</label><div class="hstack"><select id="bPar" title="How many items the model works on at once">
       <option value="1" ${(S.state.config.batch_parallel || 1) == 1 ? "selected" : ""}>1 at a time (best for a 4 GB GPU)</option>
       <option value="2" ${S.state.config.batch_parallel == 2 ? "selected" : ""}>2 at a time</option></select>
     <button id="bQuick20" title="Run only the first 20 items to check the output before a big job">🧪 Quick 20</button>
     <span class="muted small" id="bParNote">2 at a time only helps if Ollama runs with OLLAMA_NUM_PARALLEL=2 and the model fits twice in VRAM; on a GTX 1650 keep 1.</span></div></div>
   ${rep ? `<div class="row"><label>Repeat</label><div class="hstack"><input id="bCount" type="number" min="1" max="10000" value="${prefill.count || 10}" style="width:120px"> <span class="muted">times (1–10,000)</span></div></div>
     <div class="row"><label>Topic</label><input id="bTopic" placeholder="used for {topic}, e.g. heart failure, insulin types, infection control" value="${esc(prefill.topic || "")}"></div>`
   : `<div class="row"><label>Input</label><div>
      <div class="hstack"><button id="bPickFile">📄 Choose file…</button><button id="bPickFolder">📁 Choose folder…</button><span id="bSrc" class="muted">${BS.sourceLabel ? esc(BS.sourceLabel) : "JSON, CSV, JSONL, TXT, MD, HTML, or a .zip of HTML pages"}</span></div>
      <input type="file" id="bFile" hidden accept=".json,.csv,.tsv,.jsonl,.ndjson,.txt,.md,.markdown,.html,.htm,.zip">
      <input type="file" id="bFolder" hidden webkitdirectory multiple></div></div>
     <div class="row"><label>Split into</label><div class="hstack"><select id="bSplit"><option value="">Auto-detect</option></select>
       <span class="muted small">start at item</span><input id="bFrom" type="number" min="1" value="1" style="width:90px"><span class="muted small">how many (0 = all)</span><input id="bLimit" type="number" min="0" value="0" style="width:90px"></div></div>
     <div id="bPreview" class="bprev muted small">Choose a file to see how it will be split.</div>`}
   <div class="row"><label>Prompt</label><div><textarea id="bPrompt" class="f" rows="7" placeholder="${rep ? "e.g. Write one original quiz question about {topic}. This is number {i} of {total}." : "e.g. Rewrite this question in original wording: {item}"}"></textarea>
     <div class="muted small">Placeholders: ${rep ? "<code>{i}</code> run number, <code>{total}</code> count, <code>{topic}</code> the topic, <code>{prev}</code> previous output" : "<code>{item}</code> the item, <code>{i}</code> its number, <code>{total}</code> item count"}.</div></div></div>
   ${rep ? `<label class="hstack small"><input type="checkbox" id="bChain" style="width:auto"> Feed the previous output into the next run (<code>{prev}</code>)</label>` : ""}
   <details class="bopts" ${rep ? "" : "open"}><summary>Checks &amp; options</summary>
     <label class="hstack small"><input type="checkbox" id="bVerify" style="width:auto"> Verify pass: a second model call checks each output; failed items are retried, then flagged for review</label>
     <div class="row"><label>Verify prompt</label><textarea id="bVerifyPrompt" class="f" rows="4" placeholder="Use {original} and {output}. Leave empty for a generic check."></textarea></div>
     ${rep ? "" : `<label class="hstack small"><input type="checkbox" id="bMcq" style="width:auto"> MCQ checks: question, ≥2 distinct options, answer key matches an option, explanation, wording changed</label>`}
     <div class="row"><label>JSON schema</label><textarea id="bSchema" class="f mono" rows="3" placeholder='"auto-mcq" = same fields as the input + MCQ rules, or paste a JSON schema. Empty = no schema check.'></textarea></div>
     ${rep ? "" : `<div class="row"><label>Add fields</label><input id="bAdd" placeholder="e.g. explanation (comma separated)"></div>`}
     <div class="row"><label>Retries</label><div class="hstack"><input id="bRetries" type="number" min="0" max="5" value="2" style="width:80px"><span class="muted small">chunk size</span><input id="bChunk" type="number" min="800" max="20000" step="100" value="3500" style="width:100px"><span class="muted small">characters per model call (fits ~4k-token models)</span></div></div>
     ${rep ? "" : `<label class="hstack small"><input type="checkbox" id="bIncl" style="width:auto"> Output keeps the items outside the selected range unchanged</label>`}
   </details>
   <div class="hstack bsave"><input id="bTplName" placeholder="Template name"><button id="bTplSave">💾 Save as template</button><span id="bTplMsg" class="muted small"></span></div>
   <div id="bEst" class="best hidden"></div>
   <div id="bPerm"></div>
   <div class="hstack" style="justify-content:flex-end;margin-top:8px"><button id="bEstimate">⏱ Estimate time</button><button class="primary" id="bGo">▶ Start job…</button></div>
   <div id="bErr" class="err hidden"></div>`;
  const tplSel = $("#bTpl"); BS.tplMcq = false;
  tplSel.onchange = () => applyTpl(BS.templates.find(t => String(t.id) === tplSel.value));
  $("#bTplDel").onclick = async () => { await api("/api/batch/templates/" + tplSel.value, { method: "DELETE" }); BS.templates = (await api("/api/batch/templates")).templates; renderBatch(); };
  $("#bTplSave").onclick = saveTpl;
  $("#bEstimate").onclick = estimate; $("#bGo").onclick = startJob;
  if (rep && $("#bChain")) $("#bChain").onchange = () => { $("#bPar").disabled = $("#bChain").checked; if ($("#bChain").checked) $("#bPar").value = "1"; };
  $("#bQuick20").onclick = () => {
    if (rep) $("#bCount").value = 20; else { $("#bFrom").value = 1; $("#bLimit").value = 20; schedPreview(); }
    BS.estimate = null; $("#bEst").classList.remove("hidden");
    $("#bEst").innerHTML = '🧪 <b>Quick 20:</b> set to the first 20 items. Start the job, check the output, then set "how many" back to 0 (all) for the full run.';
  };
  if (!rep) {
    $("#bPickFile").onclick = () => $("#bFile").click(); $("#bPickFolder").onclick = () => $("#bFolder").click();
    $("#bFile").onchange = e => uploadInput([...e.target.files], false); $("#bFolder").onchange = e => uploadInput([...e.target.files], true);
    ["#bSplit", "#bFrom", "#bLimit", "#bMcq", "#bAdd", "#bChunk"].forEach(s => $(s).addEventListener("change", schedPreview));
    const body = $("#bBody");
    body.ondragover = e => { e.preventDefault(); body.classList.add("drag"); }; body.ondragleave = () => body.classList.remove("drag");
    body.ondrop = e => { e.preventDefault(); body.classList.remove("drag"); if (e.dataTransfer.files.length) uploadInput([...e.dataTransfer.files], e.dataTransfer.files.length > 1); };
  }
  if (prefill.prompt != null) $("#bPrompt").value = prefill.prompt;
  else if (!rep && tpls.length && !$("#bPrompt").value) { /* start empty; user picks a template */ }
  if (prefill.template) { const t = tpls.find(t => t.name.toLowerCase().includes(prefill.template.toLowerCase())); if (t) { tplSel.value = t.id; applyTpl(t); } }
  if (BS.source && !rep) schedPreview();
}
function applyTpl(t) {
  $("#bTplDel").classList.toggle("hidden", !t || t.builtin); $("#bTplDesc").textContent = t ? t.description : "";
  BS.tplMcq = !!(t && t.mcq);
  if (!t) return;
  $("#bPrompt").value = t.prompt; $("#bVerify").checked = t.verify; $("#bVerifyPrompt").value = t.verify_prompt || ""; $("#bSchema").value = t.schema || "";
  if ($("#bMcq")) $("#bMcq").checked = t.mcq; if ($("#bAdd")) $("#bAdd").value = (t.add_fields || []).join(", ");
  if ($("#bSplit") && t.split) { BS.wantSplit = t.split; }
  $("#bTplName").value = t.builtin ? "" : t.name; schedPreview();
}
function spec() {
  const v = s => $(s) ? $(s).value : undefined, c = s => $(s) ? $(s).checked : undefined;
  const sp = { mode: BS.mode === "repeat" ? "repeat" : "file", prompt: v("#bPrompt"), verify: c("#bVerify"), verify_prompt: v("#bVerifyPrompt"),
    schema: (v("#bSchema") || "").trim(), retries: +v("#bRetries"), chunk_chars: +v("#bChunk"), parallel: +v("#bPar") || 1 };
  if (sp.mode === "repeat") Object.assign(sp, { count: +v("#bCount"), chain: c("#bChain"), topic: (v("#bTopic") || "").trim(), mcq: !!BS.tplMcq });
  else Object.assign(sp, { source: BS.source, split: v("#bSplit") || BS.wantSplit || "", start: +v("#bFrom") || 1, limit: +v("#bLimit") || 0, mcq: c("#bMcq"),
    add_fields: (v("#bAdd") || "").split(",").map(x => x.trim()).filter(Boolean), include_unprocessed: c("#bIncl") });
  return sp;
}
async function uploadInput(files, folder) {
  if (!files.length) return;
  const err = $("#bErr"); err.classList.add("hidden");
  $("#bSrc").textContent = `Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`;
  let set = "", r;
  try {
    for (const f of files) {
      const name = folder && f.webkitRelativePath ? f.webkitRelativePath : f.name;
      const res = await fetch("/api/batch/upload", { method: "POST", headers: { "X-Jiv-Token": TOKEN, "X-Filename": encodeURIComponent(name), "X-Set": set }, body: f });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      r = await res.json(); set = r.set;
    }
    BS.source = "set:" + set; BS.sourceLabel = `${r.name}${r.kind !== "file" ? ` (${r.files} files)` : ""}`; $("#bSrc").textContent = BS.sourceLabel;
    BS.wantSplit = BS.wantSplit && /html/.test(BS.wantSplit) && !/\.(html?|zip)$/i.test(r.name) && r.kind === "file" ? "" : BS.wantSplit;
    if ($("#bSplit")) $("#bSplit").value = ""; schedPreview(true);
  } catch (e) { $("#bSrc").textContent = "Upload failed"; err.textContent = e.message; err.classList.remove("hidden"); }
}
function schedPreview(now) { clearTimeout(BS.pvTimer); BS.pvTimer = setTimeout(preview, now === true ? 0 : 350); }
async function preview() {
  if (BS.mode !== "file" || !BS.source || !$("#bPreview")) return;
  const box = $("#bPreview");
  try {
    const sp = spec(); if (!sp.prompt) sp.prompt = "(preview)";
    const p = await api("/api/batch/preview", { method: "POST", body: sp }); BS.preview = p;
    const sel = $("#bSplit"), cur = sel.value;
    sel.innerHTML = `<option value="">Auto-detect (${esc(p.splits[p.auto_split] || p.auto_split)})</option>` + Object.entries(p.splits).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("");
    sel.value = cur || (BS.wantSplit && BS.wantSplit !== p.auto_split ? BS.wantSplit : "");
    box.innerHTML = `<b>${p.total_items.toLocaleString()} item${p.total_items !== 1 ? "s" : ""}</b> found in ${esc(p.label)} · split: ${esc(p.splits[p.split] || p.split)} ·
      selected <b id="bSelCount">${p.selected.toLocaleString()}</b> (items ${p.first_item}–${p.first_item + p.selected - 1}) · ${p.units.toLocaleString()} model call${p.units !== 1 ? "s" : ""}${p.verify ? " + verify" : ""}
      ${p.fields.length ? `<div>Fields: ${p.fields.map(f => `<code>${esc(f)}</code>`).join(" ")}</div>` : ""}
      ${p.warnings.map(w => `<div class="warnline">⚠ ${esc(w)}</div>`).join("")}
      <div class="bsample">${p.sample.map(s => `<div><b>${esc(s.label)}</b> <span>${esc(s.text)}</span></div>`).join("")}</div>`;
  } catch (e) { box.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}
async function estimate() {
  const box = $("#bEst"); box.classList.remove("hidden"); box.innerHTML = `<div class="hstack"><div class="spinner sm"></div> Running one test item through ${esc(S.state.config.model || "the model")}…</div>`;
  try {
    const e = await api("/api/batch/estimate", { method: "POST", body: spec() }); BS.estimate = e;
    box.innerHTML = `<b>⏱ Estimated time: ${fmtDur(e.eta_secs)}</b> for ${e.items.toLocaleString()} item${e.items !== 1 ? "s" : ""} (${e.units.toLocaleString()} unit${e.units !== 1 ? "s" : ""})
      <div class="muted small">Measured on 1 test item after a warm-up${e.warmup_secs != null ? ` (${e.warmup_secs}s, loads the model)` : ""}: ${e.per_unit_secs ?? e.seconds_test}s per unit, ${e.tokens_out} tokens out, <b>${e.tok_per_s ?? "?"} tokens/s</b> with ${esc(e.model)}${e.parallel > 1 ? `, ${e.parallel} at a time` : ""}. ${esc(e.basis)}. Real time varies with item length and retries; the job's ETA updates from measured speed as it runs.</div>
      ${e.problems.length ? `<div class="warnline">⚠ The test item would need a retry: ${esc(e.problems.join("; "))}</div>` : `<div class="okline">✓ Test item passed${e.verify ? " the verify pass" : " the checks"}.</div>`}
      <details><summary class="small">Test output</summary><pre class="mini-log">${esc(e.sample_output)}</pre></details>`;
  } catch (err) { box.innerHTML = `<span class="err">${esc(err.message)}</span>`; }
}
async function startJob() {
  const err = $("#bErr"); err.classList.add("hidden");
  try {
    const sp = spec(); const p = await api("/api/batch/preview", { method: "POST", body: sp });
    if (!p.selected) throw new Error("No items to process.");
    const what = p.mode === "repeat" ? `Run the prompt ${p.selected.toLocaleString()} time(s)${p.topic ? ` (topic: ${p.topic})` : ""}` :
      `Run the prompt on ${p.selected.toLocaleString()} item(s) of ${p.label} (items ${p.first_item}–${p.first_item + p.selected - 1} of ${p.total_items.toLocaleString()}, split: ${p.split})`;
    const exact = `${what}${sp.verify ? `\n+ verify pass on each item, up to ${sp.retries} retries, then flag for review` : ""}\nModel: ${p.model} · ${p.units.toLocaleString()} model call(s)${sp.verify ? " + verify calls" : ""}${BS.estimate ? ` · est. ${fmtDur(BS.estimate.eta_secs)}` : ""}\n\nPrompt:\n${sp.prompt.slice(0, 600)}${sp.prompt.length > 600 ? "…" : ""}\n\nOutput folder: ${p.output_dir}`;
    $("#bGo").disabled = true;
    const ok = await askPermission($("#bPerm"), "Start batch job", exact, "start_batch", { items: p.selected });
    $("#bGo").disabled = false;
    if (!ok) return;
    const j = await api("/api/batch/jobs", { method: "POST", body: { spec: sp, approved: true, job_id: p.job_id, estimate: BS.estimate } });
    BS.open = j.id; BS.estimate = null; BS.mode = "jobs"; renderBatch();
  } catch (e) { $("#bGo").disabled = false; err.textContent = e.message; err.classList.remove("hidden"); }
}
async function saveTpl() {
  const name = $("#bTplName").value.trim(), msg = $("#bTplMsg"); if (!name) { msg.textContent = "Give the template a name first."; return; }
  const sp = spec();
  try {
    const t = await api("/api/batch/templates", { method: "POST", body: { name, mode: sp.mode, prompt: sp.prompt, verify: sp.verify, verify_prompt: sp.verify_prompt, schema: sp.schema, mcq: sp.mcq, add_fields: sp.add_fields, split: sp.split || "" } });
    BS.templates = (await api("/api/batch/templates")).templates; msg.textContent = "✓ Saved";
    const sel = $("#bTpl"); if (![...sel.options].some(o => o.value === String(t.id))) sel.insertAdjacentHTML("beforeend", `<option value="${t.id}">${esc(t.name)}</option>`); sel.value = t.id;
  } catch (e) { msg.textContent = e.message; }
}
const ST = { queued: "⏳ queued", running: "▶ running", paused: "⏸ paused", done: "✅ done", cancelled: "⛔ cancelled", failed: "⚠ failed" };
async function refreshJobs() {
  const host = $("#bJobs"); if (!host) return;
  const { jobs } = await api("/api/batch/jobs");
  if ($("#bJobCount")) { const a = jobs.filter(j => ["running", "queued", "paused"].includes(j.status)).length; $("#bJobCount").textContent = jobs.length ? `(${a ? a + " active" : jobs.length})` : ""; }
  if (!jobs.length) { host.innerHTML = `<div class="muted">No batch jobs yet. Start one from "Repeat N times" or "Run over a file".</div>`; return; }
  const openId = BS.open;
  if (host.querySelector(".muted:not(.bjob *)")) host.innerHTML = "";
  const ids = jobs.map(j => j.id);
  [...host.querySelectorAll(".bjob")].forEach(el => { if (!ids.includes(el.dataset.jid)) el.remove(); });
  jobs.forEach((j, n) => {
    let el = host.querySelector(`.bjob[data-jid="${j.id}"]`);
    if (!el) { el = document.createElement("div"); el.dataset.jid = j.id; el.innerHTML = `<div class="bj-main"></div><div class="bj-log hidden" id="bLog-${j.id}"></div>`; }
    if (host.children[n] !== el) host.insertBefore(el, host.children[n] || null);
    el.className = "bjob " + j.status;
    el.querySelector(".bj-main").innerHTML = jobHtml(j, openId);
    el.querySelector(".bj-log").classList.toggle("hidden", openId !== j.id);
    el.querySelectorAll("[data-bact]").forEach(b => b.onclick = async () => {
      const a = b.dataset.bact;
      if (a === "cancel" && !confirm("Cancel this job? Finished items are kept and can still be downloaded.")) return;
      if (a === "delete") { if (!confirm("Delete this job and its output files?")) return; await api("/api/batch/jobs/" + j.id, { method: "DELETE" }); }
      else await api(`/api/batch/jobs/${j.id}/${a}`, { method: "POST" }).catch(e => alert(e.message));
      refreshJobs();
    });
    el.querySelector("[data-blog]").onclick = () => { BS.open = BS.open === j.id ? null : j.id; refreshJobs(); };
  });
  if (openId && $("#bLog-" + openId)) {
    const [j, fl] = await Promise.all([api(`/api/batch/jobs/${openId}?log=40`), api(`/api/batch/jobs/${openId}/items?status=flagged&limit=100`)]);
    const lg = $("#bLog-" + openId); if (!lg) return;
    const html = `<pre class="mini-log">${esc(j.log.map(l => `${new Date(l.ts * 1000).toLocaleTimeString()}  ${l.text}`).join("\n"))}</pre>
      ${fl.count ? `<h4>⚠ Flagged for review (${fl.count})</h4><table class="audit"><tr><th>#</th><th>Item</th><th>Problems</th><th>Attempts</th></tr>${fl.items.map(i => `<tr><td>${i.idx + 1}</td><td>${esc(i.label)}</td><td class="small">${esc(i.problems)}</td><td>${i.attempts}</td></tr>`).join("")}</table>` : ""}`;
    if (lg.dataset.html !== html) { const pre = lg.querySelector("pre"), atEnd = !pre || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4; lg.innerHTML = html; lg.dataset.html = html; const np = lg.querySelector("pre"); if (atEnd && np) np.scrollTop = np.scrollHeight; }
  }
}
function jobHtml(j, openId) {
  return `<div class="hstack bj-head"><b>${esc(j.name)}</b><span class="tag">${ST[j.status] || j.status}</span><span class="muted small">${j.total.toLocaleString()} item${j.total !== 1 ? "s" : ""}${j.spec.verify ? " · verify on" : ""}${j.parallel > 1 ? ` · ${j.parallel} at a time` : ""}</span>
      <span class="grow"></span>
      ${["running", "queued"].includes(j.status) ? `<button data-bact="pause">⏸ Pause</button>` : ""}${["paused", "failed"].includes(j.status) ? `<button data-bact="resume" class="primary">▶ Resume</button>` : ""}
      ${["running", "queued", "paused", "failed"].includes(j.status) ? `<button data-bact="cancel" class="danger">Cancel</button>` : `<button data-bact="delete" class="ghost" title="Delete job and its files">🗑</button>`}</div>
    <div class="bar"><span style="width:${j.percent}%"></span></div>
    <div class="small bj-stats"><b>${j.done.toLocaleString()} / ${j.total.toLocaleString()}</b> (${j.percent}%) · <span class="${j.flagged ? "warnline" : ""}">${j.flagged} flagged</span>
      ${j.eta_secs != null ? ` · ETA ${fmtDur(j.eta_secs)}` : ""}${j.avg_secs ? ` · ${j.avg_secs.toFixed(1)} s/item` : ""}${j.tok_per_s ? ` · ${j.tok_per_s} tok/s` : ""}
      ${j.estimate && j.status !== "done" ? ` · first estimate ${fmtDur(j.estimate.eta_secs)}` : ""}${j.error ? `<div class="err">${esc(j.error)}</div>` : ""}</div>
    <div class="hstack small bj-files">${j.done ? `<a class="btnlink" href="${dl(j.id, resultName(j))}" data-dl="result">⤓ Result</a><a class="btnlink" href="${dl(j.id, "review.xlsx")}" data-dl="xlsx">⤓ Review (Excel)</a><a class="btnlink" href="${dl(j.id, "review_flagged.csv")}" data-dl="csv">⤓ Review (CSV)</a>` : ""}
      <span class="muted" title="${esc(j.output_dir)}">📁 ${esc(j.output_dir)}</span><button class="link" data-blog>${openId === j.id ? "Hide log" : "Show log"}</button></div>`;
}
function resultName(j) { return j.result_name || (j.files || []).find(f => !f.startsWith("review")) || "results.md"; }
document.addEventListener("click", e => { const b = e.target.closest("[data-openbatch]"); if (b) openBatch(b.dataset.openbatch ? JSON.parse(b.dataset.openbatch) : { tab: "jobs" }); });

// "repeat this 50 times" in chat opens a prefilled Batch page instead of flooding the chat
function batchIntercept(text) {
  const t = text.trim();
  let m = t.match(/^(?:please\s+)?(?:repeat|run|do)\s+(?:this|that|it|the\s+(?:last|previous)\s+(?:prompt|message))\s+(\d{1,5})\s*(?:times|x)\b[.!]?\s*$/i);
  let prompt = null, n;
  if (m) {
    n = +m[1];
    const users = [...document.querySelectorAll(".msg.user .body")].map(x => x.innerText.trim()).filter(Boolean);
    prompt = users.length ? users[users.length - 1].split("\n")[0] : "";
  } else if ((m = t.match(/^([\s\S]{3,}?)[\s,.;:–-]+(?:and\s+)?(?:repeat|run|do)\s+(?:this|that|it)\s+(\d{1,5})\s*(?:times|x)\b[.!]?\s*$/i))) { prompt = m[1].trim(); n = +m[2]; }
  else if ((m = t.match(/^(?:please\s+)?(?:repeat|run|do)\s+(\d{1,5})\s*(?:times|x)\s*[:\-–]\s*([\s\S]{3,})$/i))) { n = +m[1]; prompt = m[2].trim(); }
  if (!m) return false;
  openBatch({ mode: "repeat", prompt: prompt || "", count: Math.min(10000, Math.max(1, n)) });
  return true;
}
document.addEventListener("DOMContentLoaded", () => { const b = $("#openBatch"); if (b) b.onclick = () => openBatch(); });
