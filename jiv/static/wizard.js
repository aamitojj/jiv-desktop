// First-run Setup Wizard + Manage models
const W = { hw: null, rec: null, model: null, status: null, poll: null };
const fmtGB = g => g == null ? "?" : (g >= 10 ? Math.round(g) : g.toFixed(1)) + " GB";
const fmtETA = s => s == null ? "–" : s < 60 ? s + "s" : s < 3600 ? Math.floor(s / 60) + "m " + (s % 60) + "s" : Math.floor(s / 3600) + "h " + Math.floor(s % 3600 / 60) + "m";

// "by NVIDIA" badge: catalog vendor, or a Nemotron / Mistral-NeMo name for installed models
const isNvidiaName = n => /^(nemotron|mistral-nemo)/i.test(n || "");
function nvBadge(m) {
  const v = m.vendor || (isNvidiaName(m.name) ? (/^mistral-nemo/i.test(m.name) ? "NVIDIA + Mistral AI" : "NVIDIA") : "");
  if (!/NVIDIA/.test(v)) return "";
  return `<span class="tag nv" title="${esc(m.license ? "Licence: " + m.license : v)}">by ${esc(v)}</span>`;
}
function wizShell(step, body) {
  const steps = ["Your PC", "Choose model", "Install Ollama", "Download", "Finish"];
  modal(`<div class="wiz"><div class="wiz-head"><h2>🧙 Setup Wizard</h2><button class="icon" data-close="1" title="Close">✕</button></div>
   <div class="steps">${steps.map((s, i) => `<div class="stp ${i < step ? "done" : i === step ? "cur" : ""}"><span>${i < step ? "✓" : i + 1}</span>${s}</div>`).join("")}</div>
   <div id="wizBody">${body}</div></div>`);
  $("#modalCard").classList.add("wide");
}
// In-wizard permission card -> Promise<boolean>
function askPermission(host, title, exact, action, detail) {
  if (S.state?.config?.auto_approve_all) {   // Settings → "Auto-approve all actions": show what ran, no prompt
    const d = document.createElement("div"); d.className = "perm done auto";
    d.innerHTML = `<h4>⚡ ${esc(title)}</h4><div class="muted">Auto-approved (Settings → Permissions). Jiv is doing exactly this:</div><div class="cmd">${esc(exact)}</div>`;
    host.innerHTML = ""; host.append(d);
    return Promise.resolve(true);
  }
  return new Promise(res => {
    const d = document.createElement("div"); d.className = "perm";
    d.innerHTML = `<h4>🔐 ${esc(title)}</h4><div class="muted">Jiv will do exactly this:</div><div class="cmd">${esc(exact)}</div>
      <div class="btns"><button class="ok" data-a="1">Allow</button><button class="danger" data-a="0">Deny</button></div>`;
    host.innerHTML = ""; host.append(d);
    d.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => {
      const ok = b.dataset.a === "1"; d.classList.add("done");
      d.querySelector(".btns").innerHTML = `<span class="muted">${ok ? "✓ Allowed" : "⛔ Denied"}</span>`;
      if (!ok) await api("/api/setup/deny", { method: "POST", body: { action, detail } });
      res(ok);
    });
  });
}
function progressHTML(id) {
  return `<div class="prog" id="${id}"><div class="bar"><div class="fill" style="width:0%"></div></div>
   <div class="hstack prog-stats"><b class="pct">0%</b><span class="muted sz"></span><span class="muted spd"></span><span class="muted eta"></span><span class="muted phase" style="margin-left:auto"></span></div><pre class="plog hidden"></pre></div>`;
}
function renderProgress(el, j) {
  el.querySelector(".fill").style.width = j.percent + "%";
  el.querySelector(".pct").textContent = j.percent.toFixed(1) + "%";
  el.querySelector(".sz").textContent = j.total ? `${(j.completed / 1e6).toFixed(0)} / ${(j.total / 1e6).toFixed(0)} MB` : "";
  el.querySelector(".spd").textContent = j.speed_mbps ? `${j.speed_mbps.toFixed(1)} MB/s` : "";
  el.querySelector(".eta").textContent = j.status === "running" && j.total ? "ETA " + fmtETA(j.eta_s) : "";
  el.querySelector(".phase").textContent = j.status === "error" ? "❌ " + j.error : j.status === "cancelled" ? "⏸ Paused" : j.phase;
  const lg = el.querySelector(".plog"); if (j.log && j.log.length) { lg.classList.remove("hidden"); lg.textContent = j.log.slice(-8).join("\n"); }
}
function pollJob(id, el, onEnd) {
  clearInterval(W.poll);
  W.poll = setInterval(async () => {
    let j; try { j = await api("/api/jobs/" + id); } catch { return; }
    if (el.isConnected) renderProgress(el, j);
    if (j.status !== "running") { clearInterval(W.poll); onEnd(j); }
  }, 400);
}

// ---------- step 1: hardware ----------
async function openWizard() {
  wizShell(0, `<div class="center"><div class="spinner"></div><p>Scanning your PC (RAM, CPU, GPU, disk)…</p></div>`);
  const r = await api("/api/setup/hardware"); W.hw = r.hardware; W.rec = r.recommendation;
  const h = W.hw, c = h.cpu;
  const gpu = h.gpus.length ? h.gpus.map(g => `<div><b>${esc(g.name)}</b><div class="muted">${esc(g.vendor)} · ${g.unified_memory ? `${g.gpu_cores ? g.gpu_cores + "-core GPU · " : ""}unified memory · Metal budget ${fmtGB(g.metal_budget_gb)}` : "VRAM " + (g.vram_gb ? fmtGB(g.vram_gb) : "unknown")} <span class="src">via ${esc(g.source)}</span></div></div>`).join("") : '<div class="muted">No dedicated GPU found — models will run on the CPU.</div>';
  wizShell(0, `<p class="muted">Here's what I found. I'll use this to pick a free AI model that runs well on your computer.</p>
   <div class="hwgrid">
    <div class="hw"><div class="ic">${h.platform === "darwin" ? "🍎" : h.platform === "win32" ? "🪟" : "🐧"}</div><div class="k">System</div><div class="v">${esc(h.os)}</div></div>
    <div class="hw"><div class="ic">🧠</div><div class="k">Memory (RAM)</div><div class="v">${fmtGB(h.ram_total_gb)} total</div><div class="muted">${fmtGB(h.ram_free_gb)} free now</div></div>
    <div class="hw"><div class="ic">⚙️</div><div class="k">Processor</div><div class="v">${esc(c.name)}</div><div class="muted">${c.cores ?? "?"} cores / ${c.threads ?? "?"} threads · ${c.apple_silicon ? `Apple Silicon${c.performance_cores ? ` · ${c.performance_cores}P + ${c.efficiency_cores}E` : ""}` : `AVX2 ${c.avx2 === true ? "✅ yes" : c.avx2 === false ? "❌ no" : "?"}`}</div></div>
    <div class="hw"><div class="ic">🎮</div><div class="k">Graphics</div><div class="v">${gpu}</div></div>
    <div class="hw"><div class="ic">💾</div><div class="k">Free disk</div><div class="v">${fmtGB(h.disk.free_gb)}</div><div class="muted">of ${fmtGB(h.disk.total_gb)} · ${esc(h.disk.path)}</div></div>
    <div class="hw"><div class="ic">📐</div><div class="k">AI memory budget</div><div class="v">${W.rec.budget.vram_gb ? fmtGB(W.rec.budget.vram_gb) + " GPU + " : ""}${fmtGB(W.rec.budget.ram_gb)} RAM</div><div class="muted">after leaving room for ${h.platform === "darwin" ? "macOS" : h.platform === "win32" ? "Windows" : "the OS"}</div></div>
   </div>
   <div class="hstack wiz-nav"><button data-close="1">Skip setup</button><button class="primary" id="wNext">Next: choose a model →</button></div>`);
  $("#wNext").onclick = wizChoose;
}

// ---------- step 2: choose ----------
function modelRow(m, rec) {
  return `<label class="mrow ${rec ? "rec" : ""}"><input type="radio" name="wm" value="${esc(m.name)}" ${rec ? "checked" : ""}>
   <div class="mmain"><div><b>${esc(m.name)}</b> ${rec ? '<span class="tag good">★ Recommended</span>' : ""} ${m.tools ? '<span class="tag">🔧 tools</span>' : '<span class="tag warn">no native tools</span>'} ${m.kind === "coder" ? '<span class="tag">💻 code</span>' : ""}${m.kind === "multimodal" ? '<span class="tag">🖼 vision</span>' : ""} ${nvBadge(m)}</div>
   <div class="muted">${esc(m.desc)}${m.ctx ? ` · ${esc(m.ctx)} context` : ""}</div><div class="reason">${esc(m.reason)}</div></div>
   <div class="mside"><div>${fmtGB(m.size_gb)}</div><div class="spd-${m.speed.replace(/\s/g, "")}">${esc(m.speed)}</div><div class="muted">${m.mode}</div></div></label>`;
}
function wizChoose() {
  const R = W.rec;
  wizShell(1, `${R.recommended ? modelRow(R.recommended, true) : '<div class="err">No catalog model fits comfortably — try a custom small model.</div>'}
   <details ${R.recommended ? "" : "open"}><summary>Alternatives that fit this computer (${R.alternatives.length})</summary>${R.alternatives.map(m => modelRow(m, false)).join("")}</details>
   <details><summary class="muted">Too big for this computer (${R.too_big.length})</summary>${R.too_big.map(m => `<div class="muted small">${esc(m.name)} · ${fmtGB(m.size_gb)} — ${esc(m.reason)}</div>`).join("")}</details>
   <label class="mrow"><input type="radio" name="wm" value="__custom"><div class="mmain"><b>Custom model</b><div class="muted">Any model from ollama.com/library, e.g. <code>gemma3:4b</code></div><input id="wCustom" placeholder="model:tag"></div></label>
   <div class="hstack wiz-nav"><button id="wBack">← Back</button><button class="primary" id="wNext">Next →</button></div>`);
  $("#wCustom").onfocus = () => document.querySelector('input[value="__custom"]').checked = true;
  $("#wBack").onclick = openWizard;
  $("#wNext").onclick = () => {
    let v = document.querySelector('input[name="wm"]:checked')?.value;
    if (v === "__custom") v = $("#wCustom").value.trim();
    if (!v) return alert("Pick a model or type a custom name.");
    W.model = v; wizOllama();
  };
}

// ---------- step 3: Ollama ----------
async function wizOllama() {
  const s = W.status = await api("/api/ollama/status");
  if (s.running) return wizDownload();
  const win = s.install_mode === "windows", mac = s.install_mode === "mac";
  wizShell(2, `<p>Jiv uses <b>Ollama</b> (free, open source) to run AI models on your computer.</p>
   <div class="statusline">${s.installed ? "✅ Ollama is installed but not running." : "⬇️ Ollama is not installed yet."}</div>
   <div id="wAct" class="hstack" style="margin:12px 0">${s.installed
      ? '<button class="primary" id="wStart">▶ Start Ollama</button>'
      : win ? `<button class="primary" id="wInst">Download & install Ollama</button>${s.winget_available ? '<button id="wWinget">Use winget instead</button>' : ""}`
      : mac ? `${s.brew_available ? '<button class="primary" id="wBrew">🍺 Install with Homebrew</button><button id="wZip">Download Ollama.app instead</button>' : '<button class="primary" id="wZip">Download & install Ollama.app</button>'}`
      : '<button class="primary" id="wInst">Install with the official script</button>'}
      <button id="wRecheck">↻ Re-check</button></div>
   <div class="muted small">Prefer doing it yourself? ${win ? `Run <code>${esc(s.winget_cmd)}</code> or download <a href="https://ollama.com/download/windows" target="_blank">ollama.com/download</a>` : mac ? `Run <code>brew install ollama</code> in Terminal or download <a href="https://ollama.com/download/mac" target="_blank">ollama.com/download/mac</a>` : `Run <code>${esc(s.linux_cmd)}</code>`}, then press Re-check.</div>
   <div id="wPerm"></div><div id="wProg"></div>
   <div class="hstack wiz-nav"><button id="wBack">← Back</button><button class="primary" id="wNext" disabled>Next →</button></div>`);
  $("#wBack").onclick = wizChoose; $("#wRecheck").onclick = wizOllama;
  const install = async method => {
    const exact = method === "winget" ? s.winget_cmd : method === "brew" ? `brew install ollama\nbrew services start ollama   (or: ollama serve)`
      : method === "zip" ? `1. Download ${s.mac_zip_url}\n2. Unzip Ollama.app into ${s.mac_target}\n3. xattr -dr com.apple.quarantine ${s.mac_target}/Ollama.app\n4. Launch Ollama.app and wait for ${s.url}` : win ? `1. Download ${s.installer_url}\n2. Run: OllamaSetup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\n3. Start the Ollama service (ollama serve)` : s.install_mode === "linux" ? s.linux_cmd : "Open https://ollama.com/download/mac";
    if (!await askPermission($("#wPerm"), "Install Ollama on this computer", exact, "install_ollama", { method })) return;
    $("#wAct").classList.add("hidden");
    const j = await api("/api/ollama/install", { method: "POST", body: { approved: true, method } });
    $("#wProg").innerHTML = progressHTML("pInst");
    pollJob(j.id, $("#pInst"), j => { if (j.status === "done") { $("#wNext").disabled = false; $("#wNext").onclick = wizOllama; $("#wNext").click(); } else $("#wAct").classList.remove("hidden"); });
  };
  if ($("#wInst")) $("#wInst").onclick = () => install("installer");
  if ($("#wWinget")) $("#wWinget").onclick = () => install("winget");
  if ($("#wBrew")) $("#wBrew").onclick = () => install("brew");
  if ($("#wZip")) $("#wZip").onclick = () => install("zip");
  if ($("#wStart")) $("#wStart").onclick = async () => {
    if (!await askPermission($("#wPerm"), "Start the Ollama service", "ollama serve", "start_ollama", {})) return;
    $("#wProg").innerHTML = '<div class="spinner"></div>';
    const r = await api("/api/ollama/start", { method: "POST", body: { approved: true } });
    $("#wProg").innerHTML = `<div class="${r.ok ? "notice" : "err"}">${esc(r.message)}</div>`; if (r.ok) wizOllama();
  };
}

// ---------- step 4: download ----------
async function wizDownload() {
  const ms = await api("/api/ollama/models");
  const have = ms.models.find(m => m.name === W.model || m.name === W.model + ":latest");
  if (have) return wizFinish();
  const info = [W.rec?.recommended, ...(W.rec?.alternatives || [])].find(m => m && m.name === W.model);
  wizShell(3, `<p>Download <b>${esc(W.model)}</b>${info ? ` (${fmtGB(info.size_gb)})` : ""} into Ollama. You can pause and resume at any time.</p>
   <div id="wPerm"></div><div id="wProg"></div>
   <div class="hstack" id="wCtl"></div>
   <div class="hstack wiz-nav"><button id="wBack">← Back</button><button class="primary" id="wNext" disabled>Next →</button></div>`);
  $("#wBack").onclick = () => { clearInterval(W.poll); wizChoose(); };
  if (!await askPermission($("#wPerm"), "Download an AI model", `POST ${W.status?.url || "http://localhost:11434"}/api/pull  {"model": "${W.model}"}` + (info ? `\n≈ ${info.size_gb} GB download` : ""), "pull_model", { model: W.model })) {
    $("#wCtl").innerHTML = '<button id="wRetry">Ask again</button>'; $("#wRetry").onclick = wizDownload; return;
  }
  const go = async (resumed) => {
    const j = await api("/api/ollama/pull", { method: "POST", body: { approved: true, model: W.model } });
    if (!$("#pPull")) $("#wProg").innerHTML = progressHTML("pPull");
    $("#wCtl").innerHTML = '<button class="danger" id="wCancel">⏸ Pause / cancel</button>';
    $("#wCancel").onclick = () => api(`/api/jobs/${j.id}/cancel`, { method: "POST" });
    pollJob(j.id, $("#pPull"), j => {
      if (j.status === "done") { $("#wCtl").innerHTML = '<span class="notice">✅ Download complete</span>'; $("#wNext").disabled = false; $("#wNext").onclick = wizFinish; setTimeout(wizFinish, 600); }
      else { $("#wCtl").innerHTML = '<button class="primary" id="wResume">▶ Resume</button>'; $("#wResume").onclick = () => go(true); }
    });
  };
  go(false);
}

// ---------- step 5: finish ----------
async function wizFinish() {
  wizShell(4, `<div class="center"><div class="spinner"></div><p>Configuring Jiv to use <b>${esc(W.model)}</b> and running a test chat…</p></div>`);
  await api("/api/setup/finish", { method: "POST", body: { model: W.model } });
  const t = await api("/api/test", { method: "POST", body: {} });
  await refresh();
  wizShell(5, `<div class="center"><div style="font-size:48px">${t.ok ? "🎉" : "⚠️"}</div>
   <h2>${t.ok ? "You're all set!" : "Almost there"}</h2>
   <p>Provider: <b>Ollama</b> · Model: <b>${esc(W.model)}</b></p>
   ${t.ok ? `<div class="cmd" style="text-align:left">Test reply: ${esc(t.reply)}</div>` : `<div class="err">${esc(t.error)}</div>`}
   <div class="hstack" style="justify-content:center;margin-top:16px"><button data-close="1" class="primary">Start chatting →</button></div></div>`);
}

// ---------- Manage models ----------
// 1.5: one-click switch between the two tested defaults
const QUICK_MODELS = [
  { name: "huihui_ai/qwen2.5-abliterate:3b", size: "1.9 GB", best: true, note: "Recommended adult default. Refusal-removed Qwen 2.5 3B (community). Fits a 4 GB GPU; fewer built-in refusals on adult topics." },
  { name: "qwen2.5:3b", size: "1.9 GB", best: false, note: "Strong small model for articles, explanations and NCLEX MCQs. More cautious on adult topics than the abliterated default." },
  { name: "nemotron-mini:4b", size: "2.7 GB", best: false, note: "By NVIDIA. Good at short chat and tool use; weaker at long writing and MCQ answer keys." }];
const QUICK_NOTE = "All three fit a 4 GB GPU such as a GTX 1650. Switching only changes which model Jiv talks to; your chats, memory and settings stay. Also try huihui_ai/qwen3-abliterated:4b or dolphin-phi:2.7b from Download.";
const W_USE_AFTER = { name: "" };
async function openModels() {
  const [st, ms, catList] = await Promise.all([api("/api/ollama/status"), api("/api/ollama/models"), api("/api/catalog").catch(() => [])]);
  const cat = Object.fromEntries(catList.map(c => [c.name, c]));
  modal(`<div class="wiz-head"><h2>📦 Manage models</h2><button class="icon" data-close="1">✕</button></div>
   <div class="statusline">${st.running ? `✅ Ollama ${esc(st.version)} running at ${esc(st.url)}` : st.installed ? "⚠️ Ollama installed but not running" : "⬇️ Ollama not installed"}
     ${st.running ? "" : '<button id="mWiz">Open Setup Wizard</button>'}</div>
   <table class="audit"><tr><th>Model</th><th>Size</th><th>Params</th><th>Quant</th><th></th></tr>
   ${ms.models.map(m => `<tr><td><b>${esc(m.name)}</b> ${nvBadge(cat[m.name] || cat[m.name.replace(/:latest$/, "")] || { name: m.name })}${m.name === S.state.config.model ? ' <span class="tag good">in use</span>' : ""}</td><td>${fmtGB(m.size_gb)}</td><td>${esc(m.params)}</td><td>${esc(m.quant)}</td>
     <td class="hstack"><button data-use="${esc(m.name)}">Use</button><button class="danger" data-del="${esc(m.name)}">Delete</button></td></tr>`).join("") || `<tr><td colspan=5 class="muted">${st.running ? "No models installed yet." : "Start Ollama to see models."}</td></tr>`}
   </table><div id="mPerm"></div>
   <div class="quick" id="mQuick"><h3>⚡ Quick switch</h3>${QUICK_MODELS.map(q => { const have = ms.models.some(m => m.name === q.name || m.name === q.name + ":latest"), use = S.state.config.model === q.name;
     return `<div class="qcard ${use ? "on" : ""}"><b>${esc(q.name)}</b> ${q.best ? '<span class="tag good">recommended adult default</span>' : ""}<div class="small muted">${esc(q.note)}</div>
       ${use ? '<span class="tag good">in use</span>' : have ? `<button class="primary" data-use="${esc(q.name)}">Use ${esc(q.name)}</button>` : `<button data-qpull="${esc(q.name)}">⬇ Download & use (${esc(q.size)})</button>`}</div>`; }).join("")}
     <div class="small muted">${esc(QUICK_NOTE)}</div></div>
   <h3>Download another model</h3><div class="hstack"><input id="mName" placeholder="e.g. qwen2.5:3b, llama3.1:8b" style="flex:1" list="mList"><datalist id="mList"></datalist><button class="primary" id="mPull">⬇ Pull</button></div>
   <div id="mProg"></div><div class="hstack" id="mCtl"></div>
   <details class="uncensored" id="mUncensored" open><summary>🔓 Adult / fewer-refusal models</summary>
    <p class="small">Jiv treats you as a consenting adult by default (no toggle). Hard limits stay: no sexual content involving minors, no sexual content about real identifiable people without consent, and nothing that helps with clearly illegal harm.
     <b>Recommended default for a 4 GB GPU: huihui_ai/qwen2.5-abliterate:3b</b> (also in Quick switch above). Alternatives: huihui_ai/qwen3-abliterated:4b and dolphin-phi:2.7b. qwen2.5:3b and nemotron-mini:4b stay available.
     Abliterated / uncensored models are community uploads and are less careful with facts — double-check anything important.</p>
    <table class="audit"><tr><th>Model</th><th>Download</th><th>Fits a 4 GB GPU (GTX 1650)?</th></tr>
     <tr><td><code>huihui_ai/qwen2.5-abliterate:3b</code> <span class="tag good">recommended</span></td><td>1.9 GB</td><td>✅ fully on GPU</td></tr>
     <tr><td><code>huihui_ai/qwen3-abliterated:4b</code></td><td>2.5 GB</td><td>✅ fits (thinking model, slower replies)</td></tr>
     <tr><td><code>dolphin-phi:2.7b</code></td><td>1.6 GB</td><td>✅ fully on GPU</td></tr>
     <tr><td><code>huihui_ai/llama3.2-abliterate:3b</code></td><td>2.2 GB</td><td>✅ fully on GPU</td></tr>
     <tr><td><code>llama2-uncensored:7b</code></td><td>3.8 GB</td><td>⚠ tight: partly on CPU, slow</td></tr>
     <tr><td><code>dolphin-mistral:7b</code></td><td>4.1 GB</td><td>⚠ larger than 4 GB VRAM: partly on CPU, slow</td></tr>
    </table>
    <div class="hstack small">Type a name above and press ⬇ Pull (asks first). <button class="link" id="mUncFill">Fill in huihui_ai/qwen2.5-abliterate:3b</button></div>
   </details>`);
  document.querySelectorAll("[data-qpull]").forEach(b => b.onclick = () => { $("#mName").value = b.dataset.qpull; W_USE_AFTER.name = b.dataset.qpull; $("#mPull").click(); });
  if ($("#mUncFill")) $("#mUncFill").onclick = () => { $("#mName").value = "huihui_ai/qwen2.5-abliterate:3b"; $("#mName").focus(); };
  api("/api/setup/hardware").then(r => { const l = $("#mList"); if (l) l.innerHTML = [r.recommendation.recommended, ...r.recommendation.alternatives].filter(Boolean).map(m => `<option value="${esc(m.name)}">${esc(m.speed)} · ${fmtGB(m.size_gb)}${/NVIDIA/.test(m.vendor || "") ? " · by NVIDIA" : ""}</option>`).join(""); }).catch(() => { });
  if ($("#mWiz")) $("#mWiz").onclick = openWizard;
  document.querySelectorAll("[data-use]").forEach(b => b.onclick = async () => { await api("/api/setup/finish", { method: "POST", body: { model: b.dataset.use } }); await refresh(); openModels(); });
  document.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    const n = b.dataset.del;
    if (!await askPermission($("#mPerm"), "Delete a model", `DELETE ${st.url}/api/delete  {"model": "${n}"}`, "delete_model", { model: n })) return;
    try { await api("/api/ollama/delete", { method: "POST", body: { approved: true, model: n } }); } catch (e) { alert(e.message); }
    openModels();
  });
  $("#mPull").onclick = async () => {
    const n = $("#mName").value.trim(); if (!n) return;
    if (!await askPermission($("#mPerm"), "Download an AI model", `POST ${st.url}/api/pull  {"model": "${n}"}`, "pull_model", { model: n })) return;
    const go = async () => {
      const j = await api("/api/ollama/pull", { method: "POST", body: { approved: true, model: n } });
      $("#mProg").innerHTML = progressHTML("pM"); $("#mCtl").innerHTML = '<button class="danger" id="mCancel">⏸ Pause / cancel</button>';
      $("#mCancel").onclick = () => api(`/api/jobs/${j.id}/cancel`, { method: "POST" });
      pollJob(j.id, $("#pM"), async j => { if (j.status === "done") { if (W_USE_AFTER.name === n) { W_USE_AFTER.name = ""; await api("/api/setup/finish", { method: "POST", body: { model: n } }); await refresh(); } openModels(); } else { $("#mCtl").innerHTML = '<button class="primary" id="mRes">▶ Resume</button>'; $("#mRes").onclick = go; } });
    };
    go();
  };
}
