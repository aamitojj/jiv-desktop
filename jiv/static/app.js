const TOKEN = document.querySelector('meta[name="jiv-token"]').content;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const S = { state: null, agentId: null, convId: null, runId: null, busy: false };
const COLORS = ["#7c5cff", "#22b8cf", "#f59e0b", "#ef4444", "#10b981", "#ec4899", "#6366f1", "#84cc16"];
const color = n => COLORS[[...n].reduce((a, c) => a + c.charCodeAt(0), 0) % COLORS.length];

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-Jiv-Token": TOKEN, ...(opts.headers || {}) },
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) { let e; try { e = (await r.json()).detail } catch { } throw new Error(e || r.statusText); }
  return r.json();
}
const agent = () => S.state.agents.find(a => a.id === S.agentId) || S.state.agents[0];

async function refresh() {
  S.state = await api("/api/state");
  if (!S.agentId || !S.state.agents.find(a => a.id === S.agentId)) S.agentId = S.state.agents[0].id;
  document.documentElement.dataset.theme = S.state.config.theme || "dark";
  renderSidebar(); renderTop(); renderTips();
  api("/api/updates").then(u => { const n = $("#updNotice"); const st = u.state || {};
    const msg = u.notice?.message || (st.status === "rolled_back" ? "Last update was rolled back" : "");
    n.className = msg ? "tag warn clickable" : "hidden"; n.textContent = msg ? "🔔 " + msg.slice(0, 70) : ""; }).catch(() => { });
}
function renderSidebar() {
  $("#agents").innerHTML = S.state.agents.map(a => `<div class="item ${a.id === S.agentId ? "active" : ""}" data-agent="${a.id}">
    <span class="avatar" style="background:${color(a.name)}">${esc(a.name[0].toUpperCase())}</span><span class="lbl">${esc(a.name)}${a.is_main ? ' <span class="muted">· main</span>' : ""}</span>
    <button class="act" data-edit="${a.id}" title="Edit agent">✎</button></div>`).join("");
  const convs = S.state.conversations.filter(c => c.agent_id === S.agentId);
  $("#convs").innerHTML = convs.map(c => `<div class="item ${c.id === S.convId ? "active" : ""}" data-conv="${c.id}"><span class="lbl">${esc(c.title)}</span>
    <button class="act" data-ren="${c.id}" title="Rename">✎</button><button class="act" data-delc="${c.id}" title="Delete">🗑</button></div>`).join("") || '<div class="muted" style="padding:4px 10px">No conversations yet</div>';
}
function renderTop() {
  const a = agent(); $("#agentName").textContent = a.name;
  $("#modelBadge").textContent = (a.model || S.state.config.model || "no model") + " · " + (S.state.providers[S.state.config.provider]?.label || "custom");
  $("#input").placeholder = `Message ${a.name}…  (Enter to send, Shift+Enter for a new line)`;
}
function emptyState() {
  const a = agent();
  $("#messages").innerHTML = `<div class="empty"><h1>Hi, I'm ${esc(a.name)}.</h1><div>I run on your PC with your choice of open-source AI. I'll always ask before changing anything.</div>
   ${S.state.config.model ? "" : '<p><button class="primary" data-wizard="1">Run the Setup Wizard →</button> <button data-opensettings="1">Settings</button></p>'}
   <div class="sugg">${["What's my system info?", "List the files in my Downloads folder", "Create an agent called Scout that researches topics on the web", "Search the web for the latest Ollama release"].map(s => `<button data-sugg="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;
}
async function openConv(id) {
  S.convId = id; renderSidebar();
  if (!id) return emptyState();
  const d = await api("/api/conversations/" + id); $("#messages").innerHTML = "";
  const toolEls = {};
  for (const m of d.messages) {
    if (m.role === "user") addUser(m.content, (m.meta && m.meta.attachments) || []);
    else if (m.role === "assistant") {
      const b = addBot(m.meta.agent || agent().name); setMd(b, m.content);
      if (!(m.meta.tool_calls || []).length && m.content) fbBar(b, id, m.id, (d.ratings || {})[m.id]);
      if (m.meta.stats) statsChip(b, m.meta.stats);
      if (m.meta.memories) memChip(b, m.meta.memories);
      for (const c of m.meta.tool_calls || []) toolEls[c.id] = toolCard(b.parentNode, { id: c.id, tool: c.name, args: c.arguments });
    } else if (m.role === "tool" && toolEls[m.meta.tool_call_id]) finishTool(toolEls[m.meta.tool_call_id], { result: m.content, status: /DENIED/.test(m.content) ? "denied" : /^Error/.test(m.content) ? "error" : "ok" });
  }
  scroll(true); refreshActions();
}
function addUser(t, atts) { const d = document.createElement("div"); d.className = "msg user"; d.dataset.raw = t || ""; d.innerHTML = `<div class="body">${attachmentsHtml(atts)}${t && !/^\(see attached file/.test(t) ? esc(t) : ""}</div>`; $("#messages").append(d); return d; }
function addBot(name, parent) {
  const d = document.createElement("div"); d.className = "msg bot";
  d.innerHTML = `<span class="avatar" style="background:${color(name)}">${esc(name[0].toUpperCase())}</span><div class="body"><div class="who">${esc(name)}</div><div class="md"></div></div>`;
  (parent || $("#messages")).append(d); return d.querySelector(".md");
}
// 1.5: "think first" replies show the reasoning folded, the answer below it
function mdx(t) {
  t = t || ""; const m = t.match(/<think>([\s\S]*?)(<\/think>|$)/i);
  if (!m) return md(t);
  const open = !m[2], rest = t.slice(0, m.index) + t.slice(m.index + m[0].length);
  return `<details class="think" ${open ? "open" : ""}><summary>💭 ${open ? "Thinking…" : "Reasoning (click to show)"}</summary><div class="small">${md(m[1].trim())}</div></details>` + md(rest.trim());
}
function setMd(el, t) { el.innerHTML = mdx(t); el.dataset.raw = t; }
function statsChip(mdEl, st) {
  const body = mdEl.parentNode; body.querySelector(".stats")?.remove();
  const d = document.createElement("div"); d.className = "stats small muted";
  d.textContent = `⚡ ${st.estimated ? "≈" : ""}${st.tokens} tokens · ${st.tps} tok/s · ${st.secs}s` + (st.first_token > 1.5 ? ` (first word after ${st.first_token}s)` : "");
  d.title = st.estimated ? "Token count estimated from length (this server doesn't report usage)" : "Reported by the model server";
  body.append(d);
}
function memChip(mdEl, items) {
  if (!items || !items.length) return; const body = mdEl.parentNode; body.querySelector(".memused")?.remove();
  const d = document.createElement("details"); d.className = "memused small";
  d.innerHTML = `<summary>🧠 Used ${items.length} memor${items.length === 1 ? "y" : "ies"}</summary><ul>${items.map(m => `<li><span class="tag">${esc(m.kind)}</span> ${esc(m.text)}</li>`).join("")}</ul>`;
  body.append(d);
}
// regenerate (last answer) and edit-and-resend (last message)
function refreshActions() {
  document.querySelectorAll("#messages .msgact").forEach(x => x.remove());
  if (S.busy || !S.convId) return;
  const users = [...document.querySelectorAll("#messages > .msg.user")], bots = [...document.querySelectorAll("#messages > .msg.bot")];
  const lu = users.pop(), lb = bots.pop();
  if (lu) { const b = document.createElement("button"); b.className = "msgact icon small"; b.title = "Edit and resend"; b.textContent = "✎ Edit"; b.dataset.editlast = "1"; lu.append(b); }
  if (lb && lu && lb.compareDocumentPosition(lu) & Node.DOCUMENT_POSITION_PRECEDING) {
    const b = document.createElement("button"); b.className = "msgact icon small"; b.title = "Ask the model again for a new answer"; b.textContent = "↻ Regenerate"; b.dataset.regen = "1";
    lb.querySelector(".body").append(b);
  }
}
function removeAfter(el, inclusive) {
  const kids = [...$("#messages").children]; const i = kids.indexOf(el);
  kids.slice(inclusive ? i : i + 1).forEach(k => k.remove());
}
function startEdit(userEl) {
  const raw = userEl.dataset.raw || userEl.querySelector(".body").textContent;
  const box = document.createElement("div"); box.className = "editbox";
  box.innerHTML = `<textarea id="editText" rows="3"></textarea><div class="hstack"><button class="primary" id="editSend">Save & send</button><button id="editCancel">Cancel</button></div>`;
  userEl.querySelector(".body").replaceWith(box); userEl.querySelector(".msgact")?.remove();
  $("#editText").value = raw; $("#editText").focus();
  $("#editCancel").onclick = () => openConv(S.convId);
  $("#editSend").onclick = () => { const t = $("#editText").value.trim(); if (!t) return; removeAfter(userEl, true); send(t, { edit: true }); };
}
function toolCard(parent, ev) {
  const d = document.createElement("details"); d.className = "tool"; d.dataset.id = ev.id;
  d.innerHTML = `<summary>🔧 <b>${esc(ev.tool)}</b> <span class="muted">${esc(JSON.stringify(ev.args)).slice(0, 90)}</span><span class="st run">running…</span></summary><pre>${esc(JSON.stringify(ev.args, null, 2))}</pre><pre class="res"></pre>`;
  parent.append(d); return d;
}
function finishTool(el, ev) {
  const st = el.querySelector(".st"); st.className = "st " + ev.status;
  st.textContent = { ok: "✓ done", error: "✗ error", denied: "⛔ denied" }[ev.status] || ev.status;
  el.querySelector(".res").textContent = ev.result;
}
function permCard(parent, ev) {
  const d = document.createElement("div"); d.className = "perm"; d.id = "perm-" + ev.id;
  d.innerHTML = `<h4>🔐 ${esc(ev.agent)} wants to use <code>${esc(ev.tool)}</code></h4>
   <div class="muted">Review exactly what will run:</div><div class="cmd">${esc(ev.summary)}</div>
   <details><summary class="muted">Full arguments</summary><div class="cmd">${esc(JSON.stringify(ev.args, null, 2))}</div></details>
   <div class="btns"><button class="ok" data-perm="once">Allow once</button><button data-perm="always">Always allow ${esc(ev.tool)}</button><button class="danger" data-perm="deny">Deny</button></div>`;
  d.querySelectorAll("[data-perm]").forEach(b => b.onclick = async () => {
    d.querySelectorAll("button").forEach(x => x.disabled = true);
    await api("/api/permission/" + ev.id, { method: "POST", body: { decision: b.dataset.perm } });
  });
  parent.append(d); scroll(); return d;
}
function scroll(force) { const m = $("#messages"); if (force || m.scrollHeight - m.scrollTop - m.clientHeight < 200) m.scrollTop = m.scrollHeight; }
function setBusy(b) { S.busy = b; $("#send").classList.toggle("hidden", b); $("#stop").classList.toggle("hidden", !b); $("#meter").classList.toggle("hidden", !b); if (b) { $("#meter").textContent = "waiting for the model…"; document.querySelectorAll("#messages .msgact").forEach(x => x.remove()); } }

async function send(text, opts = {}) {
  text = (text || "").trim(); if (S.busy) return;
  const regen = !!opts.regenerate, edit = !!opts.edit;
  if (!regen) {
    if (attachmentsBusy()) { alert("Wait a moment - files are still uploading."); return; }
    if (!text && !ATT.list.some(a => a.status === "ready")) return;
    if (!edit && typeof batchIntercept === "function" && !ATT.list.length && batchIntercept(text)) { $("#input").value = ""; autosize(); return; }
  }
  const atts = regen || edit ? [] : takeAttachments();
  if (!S.convId) $("#messages").innerHTML = "";
  if (!regen) addUser(text, atts);
  if (!regen && !edit) { $("#input").value = ""; autosize(); }
  setBusy(true); scroll(true);
  const stack = []; // per-depth current bot element
  let cur = null; const tools = {}; const meter = { t0: 0, chars: 0 };
  let r;
  try {
    r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json", "X-Jiv-Token": TOKEN },
      body: JSON.stringify({ message: text, agent_id: S.agentId, conv_id: S.convId, attachments: atts.map(a => a.id), regenerate: regen, edit }) });
  } catch (e) { r = { ok: false, text: async () => "Jiv's server didn't answer: " + e.message }; }
  if (!r.ok) { addBot("Jiv").innerHTML = `<div class="err">${esc(await r.text())}</div>`; setBusy(false); checkConn(); refreshActions(); return; }
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
  const container = d => d > 0 && stack.delegateBox ? stack.delegateBox : $("#messages");
  while (true) {
    const { value, done } = await reader.read(); if (done) break;
    buf += dec.decode(value, { stream: true }); let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 2);
      if (!line.startsWith("data:")) continue;
      const ev = JSON.parse(line.slice(5)); const depth = ev.depth || 0;
      switch (ev.type) {
        case "start": S.runId = ev.run_id; if (!S.convId) { S.convId = ev.conv_id; } break;
        case "step": if (stack[depth]) stack[depth].classList.remove("cursor"); stack[depth] = addBot(ev.agent, container(depth)); stack[depth].classList.add("cursor"); stack[depth].dataset.raw = ""; break;
        case "text": { const el = stack[depth]; el.dataset.raw += ev.delta; el.innerHTML = mdx(el.dataset.raw); scroll();
          if (!meter.t0) meter.t0 = performance.now(); meter.chars += ev.delta.length;
          const secs = (performance.now() - meter.t0) / 1000; if (secs > 0.3) $("#meter").textContent = `≈ ${Math.round(meter.chars / 4)} tokens · ${(meter.chars / 4 / secs).toFixed(1)} tok/s`; break; }
        case "stats": if (stack[depth]) statsChip(stack[depth], ev); break;
        case "memories_used": if (stack[0]) memChip(stack[0], ev.items); break;
        case "hint": { const n = document.createElement("div"); n.className = "notice hint"; n.textContent = "💡 " + ev.text; $("#messages").append(n); break; }
        case "replace_text": setMd(stack[depth], ev.text); meter.t0 = 0; meter.chars = 0; break;
        case "tool_start": { const host = stack[ev.depth || 0]?.parentNode || $("#messages"); tools[ev.id] = toolCard(host, ev); scroll(); break; }
        case "tool_result": tools[ev.id] && finishTool(tools[ev.id], ev); break;
        case "permission": { const t = Object.values(tools).pop(); permCard(t ? t.parentNode : $("#messages"), ev); break; }
        case "permission_resolved": { const p = $("#perm-" + ev.id); if (p) { p.classList.add("done"); p.querySelector(".btns").innerHTML = `<span class="muted">${{ once: "✓ Allowed once", always: "✓ Always allowed", deny: "⛔ Denied" }[ev.decision]}</span>`; } break; }
        case "delegate": { const box = document.createElement("div"); box.className = "delegate"; box.innerHTML = `<div class="notice">↪ Delegating to <b>${esc(ev.to)}</b>: ${esc(ev.task)}</div>`; (stack[depth]?.parentNode || $("#messages")).append(box); stack.delegateBox = box; break; }
        case "notice": { const n = document.createElement("div"); n.className = "notice"; n.textContent = ev.text; $("#messages").append(n); break; }
        case "prompt_improved": { promptImproveCard(ev); break; }
        case "agents_changed": refresh(); break;
        case "msg_saved": { const el = stack[0]; if (el) fbBar(el, ev.conv_id, ev.id); break; }
        case "memory_saved": memoryNotice(ev); break;
        case "skill_offer": skillOffer($("#messages"), ev); scroll(); break;
        case "skills_changed": break;
        case "batch_suggest": { const d = document.createElement("div"); d.className = "notice batch";
          const pre = JSON.stringify({ mode: "repeat", template: ev.template, count: ev.count, topic: ev.topic });
          d.innerHTML = `⚡ For ${ev.count.toLocaleString()} questions, the Batch page is faster and more reliable: it writes them one by one${/NCLEX/.test(ev.template) ? " with a rationale and a verify pass" : ""}, in the background, and gives you a JSON file + review Excel. <button class="link" data-openbatch="${esc(pre)}">Open the Batch MCQ generator</button>`;
          $("#messages").append(d); scroll(); break; }
        case "batch_started": { const d = document.createElement("div"); d.className = "notice batch"; d.innerHTML = `⚡ Batch job started: <b>${esc(ev.job.name)}</b> (${ev.job.total} items). <button class="link" data-openbatch>Open the Batch page</button>`; $("#messages").append(d); scroll(); break; }
        case "update_proposal": { const host = stack[depth]?.parentNode || $("#messages"); updateCard(host, { ...ev.update, diff: ev.diff }, { inChat: true }); scroll(); break; }
        case "error": { const e = document.createElement("div"); e.className = "err"; e.textContent = ev.text; $("#messages").append(e); checkConn(); break; }
        case "stopped": { const n = document.createElement("div"); n.className = "notice"; n.textContent = ev.kept ? "Stopped - the partial answer is kept. Press ↻ Regenerate for a new one." : "Stopped."; $("#messages").append(n); break; }
        case "done": break;
      }
    }
  }
  stack.forEach(e => e && e.classList.remove("cursor")); setBusy(false); S.runId = null; await refresh(); scroll(); refreshActions();
}

// ---------- 1.5: tips strip, quick model switch, model-server reconnect banner ----------
function promptImproveCard(ev) {
  const d = document.createElement("details"); d.className = "prompt-improve";
  const st = ev.status || "";
  const title = st === "used" ? "Clarified question (tap to compare)"
    : st === "unchanged" ? "Your question was already clear"
    : st === "skipped" ? "Answered your original (clarify skipped: " + (ev.reason || "") + ")"
    : st === "timeout" ? "Answered your original (clarify timed out)"
    : st === "failed" ? "Answered your original (clarify failed)"
    : st === "clarify" ? "Need a quick clarification"
    : st === "declined" ? "Model declined while clarifying — shown as-is"
    : "Prompt clarify: " + st;
  d.innerHTML = `<summary>✎ ${esc(title)}</summary>
    <div class="pi-row"><div class="muted">You wrote</div><pre class="pi-pre">${esc(ev.original || "")}</pre></div>
    ${st === "used" ? `<div class="pi-row"><div class="muted">Clarified to</div><pre class="pi-pre">${esc(ev.improved || "")}</pre></div>` : ""}
    ${ev.clarify ? `<div class="pi-row"><div class="muted">Clarification</div><pre class="pi-pre">${esc(ev.clarify)}</pre></div>` : ""}
    <div class="muted small">The clarified text is only used for this answer. It is not a system instruction. Turn off in Settings → Clarify question.</div>`;
  $("#messages").append(d); scroll();
}
function renderTips() {
  const c = S.state.config, t = $("#tips"); const small = /(^|[^\d])([0-4](\.\d)?)b\b|mini|tiny|small|phi/i.test(c.model || "");
  t.classList.toggle("hidden", !!c.tips_dismissed || !c.model || !small);
}
function quickSwitchHtml(cur) {
  if (typeof QUICK_MODELS === "undefined") return "";
  return QUICK_MODELS.map(q => `<button data-quick="${esc(q.name)}" class="${q.name === cur ? "primary" : ""}" title="${esc(q.note)}">${q.name === cur ? "✓ " : ""}${esc(q.name)}${q.best ? " ★" : ""}</button>`).join("") +
    '<span class="muted small">★ huihui_ai/qwen2.5-abliterate:3b is the recommended adult default for a 4 GB GPU</span>';
}
async function quickSwitch(name) {
  let have = false;
  try { const ms = await api("/api/ollama/models"); have = (ms.models || []).some(m => m.name === name || m.name === name + ":latest"); } catch { }
  if (!have) { closeModal(); await openModels(); $("#mName").value = name; W_USE_AFTER.name = name; $("#mPull").click(); return false; }
  await api("/api/setup/finish", { method: "POST", body: { model: name } }); await refresh(); return true;
}
const CONN = { down: false, timer: null };
async function checkConn() {
  let r; try { r = await api("/api/connection"); } catch (e) { r = { ok: false, error: "Jiv's own server is not answering", jiv_down: true }; }
  const b = $("#connBanner");
  if (r.ok) {
    const ADULT_DEF = "huihui_ai/qwen2.5-abliterate:3b";
    const cur = (S.state && S.state.config && S.state.config.model) || "";
    const suggested = S.state && S.state.config && S.state.config.adult_model_suggested;
    if (CONN.down) { b.className = "banner ok"; b.innerHTML = "✓ Reconnected to the model server."; setTimeout(() => { if (!CONN.down) b.className = "hidden"; }, 3000); }
    else if (r.model_listed === false && r.model) { b.className = "banner warn"; b.innerHTML = `⚠ The model <b>${esc(r.model)}</b> isn't on the server at ${esc(r.base_url)}. <button class="link" data-openmodels="1">Open Models</button> <button class="link" data-closebanner="1">Dismiss</button>`; }
    else if (!suggested && cur && cur !== ADULT_DEF && cur !== ADULT_DEF + ":latest") {
      b.className = "banner warn";
      b.innerHTML = `✦ New recommended default: <b>${esc(ADULT_DEF)}</b> (adult-friendly, fits a 4 GB GPU). Your current model is unchanged.
        <button class="link" data-tryadult="1">Use / download it</button> <button class="link" data-dismissadult="1">Dismiss</button>`;
    }
    else if (!/ok|warn/.test(b.className) || b.className === "banner warn") b.className = "hidden";
    CONN.down = false;
  } else if (S.state && S.state.config.model) {
    CONN.down = true; b.className = "banner err";
    b.innerHTML = r.jiv_down ? "⚠ Jiv's background server stopped. Close and reopen Jiv." :
      `⚠ Can't reach ${r.is_ollama ? "Ollama" : "the model server"} at ${esc(r.base_url)}. Jiv checks again every 10 seconds and reconnects by itself.
       <button data-reconnect="1">↻ Retry now</button>${r.is_ollama ? ' <button data-startollama="1">▶ Start Ollama</button>' : ""} <button data-opensettings="1">Settings</button>`;
  }
  clearTimeout(CONN.timer); CONN.timer = setTimeout(checkConn, CONN.down ? 10000 : 30000);
  return r;
}

// ---------- modals ----------
function modal(html) { $("#modalCard").className = "card"; $("#modalCard").innerHTML = html; $("#modal").classList.remove("hidden"); }
function closeModal() { $("#modal").classList.add("hidden"); if (typeof W !== "undefined") clearInterval(W.poll); }
$("#modal").onclick = e => { if (e.target.id === "modal") closeModal(); };

async function openSettings() {
  const c = S.state.config, P = S.state.providers;
  modal(`<h2>Settings</h2>
  <h3>AI connection</h3>
  <div class="row"><label>Provider</label><select id="s_provider">${Object.entries(P).map(([k, v]) => `<option value="${k}" ${k === c.provider ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select></div>
  <div class="row"><label>Base URL</label><input id="s_base" value="${esc(c.base_url)}"></div>
  <div class="row"><label>API key</label><input id="s_key" type="password" placeholder="only for OpenRouter / Groq / Together etc." value="${esc(c.api_key)}"></div>
  <div class="row"><label>Model</label><div class="hstack"><select id="s_model" style="flex:1"><option>${esc(c.model || "")}</option></select><button id="s_refresh">↻ Models</button></div></div>
  <div class="row"><label>Quick switch</label><div class="hstack" id="s_quick">${quickSwitchHtml(c.model)}</div></div>
  <div class="row"><label>Temperature <span id="s_tv">${c.temperature}</span></label><input id="s_temp" type="range" min="0" max="1.5" step="0.05" value="${c.temperature}"></div>
  <div class="row"><label></label><div class="hstack"><button id="s_detect">🔍 Auto-detect local AI</button><button id="s_test">⚡ Test connection</button><span id="s_msg" class="muted"></span></div></div>
  <div id="s_found"></div>
  <h3>Answer quality</h3>
  <div class="row"><label>Think first</label><div><select id="s_think"><option value="auto" ${(c.think_mode || "auto") === "auto" ? "selected" : ""}>Auto - for maths, logic and "why/compare" questions</option><option value="on" ${c.think_mode === "on" ? "selected" : ""}>Always</option><option value="off" ${c.think_mode === "off" ? "selected" : ""}>Off (fastest)</option></select>
    <div class="muted">The model reasons step by step first (shown folded as 💭), then answers. Slower, but more accurate on small models.</div></div></div>
  <div class="row"><label>Auto-retry</label><label class="hstack"><input type="checkbox" id="s_quality" style="width:auto" ${c.quality_guard !== false ? "checked" : ""}> Ask the model once more when an answer is empty, too short, a refusal, or has fewer MCQs than you asked for</label></div>
  <div class="row"><label>Backup model</label><div><label class="hstack"><input type="checkbox" id="s_fb" style="width:auto" ${c.fallback_enabled !== false ? "checked" : ""}> If the selected model errors, times out or returns an empty/garbled answer, answer once with another model that is already installed (never downloads; not used when a model declines)</label>
    <input class="f" id="s_fbm" placeholder="Preferred backup models, comma-separated (blank = smallest installed)" value="${esc((c.fallback_models || []).join(", "))}">
    <div class="muted" id="s_fbst">Checking installed models…</div></div></div>
  <div class="row"><label>Polish Jiv's English</label><label class="hstack"><input type="checkbox" id="s_polish" style="width:auto" ${c.polish_english !== false ? "checked" : ""}> Light grammar polish on Jiv's own replies (a/an, spacing, its/it's…). Never changes your words, code blocks or URLs</label></div>
  <div class="row"><label>Clarify question</label><label class="hstack"><input type="checkbox" id="s_pimpr" style="width:auto" ${c.prompt_improve !== false ? "checked" : ""}> Before answering, tidy rough wording (typos, shorthand) into a clearer question. Your original stays in the chat; you can see what changed. Off = answer exactly what you typed</label></div>
  <div class="row"><label>Vague messages</label><label class="hstack"><input type="checkbox" id="s_clarify" style="width:auto" ${c.clarity_nudge !== false ? "checked" : ""}> On a very short first message, answer and ask one clarifying question</label></div>
  <div class="row"><label>Tips strip</label><label class="hstack"><input type="checkbox" id="s_tips" style="width:auto" ${c.tips_dismissed ? "" : "checked"}> Show "tips for small models" above the chat</label></div>
  <div class="row"><label>Tool calling</label><label class="hstack"><input type="checkbox" id="s_native" style="width:auto" ${c.native_tools ? "checked" : ""}> Use native tool calling when the model supports it (falls back to JSON mode automatically)</label></div>
  <div class="row"><label>Updates</label><div class="hstack"><button id="s_updates">⬆ Updates & backups</button><span class="muted">Version ${esc(S.state.version || "")}</span></div></div>
  <div class="row"><label>Local AI</label><div class="hstack"><button id="s_wizard">🧙 Re-scan my PC / Change model</button><button id="s_models">📦 Manage models</button></div></div>
  <h3>Permissions</h3>
  <div class="row"><label>Web search</label><div>
    <input class="f" id="s_searx" placeholder="Optional SearXNG base URL (blank = DuckDuckGo + Wikipedia only)" value="${esc(c.searx_url || "")}">
    <div class="muted small">Fallback order: DuckDuckGo → SearXNG (if set) → Wikipedia for factual queries. Results and pages are cached on disk (${esc(String(c.search_cache_ttl || 3600))}s TTL). Summaries use extractive mode with [n] citations (works with no AI).</div>
  </div></div>
    <div class="row"><label>Read-only tools</label><label class="hstack"><input type="checkbox" id="s_reads" style="width:auto" ${c.auto_reads ? "checked" : ""}> Run automatically (read file, list folder, web search/fetch, clipboard, system info)</label></div>
  <div class="row"><label>All actions</label><div><label class="hstack"><input type="checkbox" id="s_autoall" style="width:auto" ${c.auto_approve_all ? "checked" : ""}> <b>Auto-approve all actions (no permission prompts)</b></label>
    <div id="s_autowarn" class="warnbox ${c.auto_approve_all ? "" : "hidden"}">⚠ <b>Warning:</b> with this on, Jiv runs shell commands, writes and deletes files, installs software, downloads models, starts batch jobs and applies updates <b>without asking you</b>. A model mistake can then change or delete files on this PC. Turn it on only if you accept that risk. Every action is still listed in the Audit log. Leave it off to get a card for each action, or use per-tool "Always allow" instead.</div></div></div>
  <div class="row"><label>Always allowed</label><div id="s_aa">${(c.always_allow || []).map(t => `<span class="chip">${esc(t)}<button data-rmaa="${esc(t)}" title="Remove">✕</button></span>`).join("") || '<span class="muted">Nothing — every change asks first (recommended)</span>'}</div></div>
  <h3>You</h3>
  <div class="row"><label>Your name</label><input id="s_user" value="${esc(c.user_name)}"></div>
  <div class="row"><label>Theme</label><select id="s_theme"><option value="dark">Dark</option><option value="light" ${c.theme === "light" ? "selected" : ""}>Light</option></select></div>
  <div class="row"><label>Custom system prompt <span class="muted">(advanced)</span></label>
    <div><textarea class="f" id="s_custom" rows="5" placeholder="Optional. Appended to every chat's system prompt. Leave blank to use the built-in rules.">${esc(c.custom_system_prompt || "")}</textarea>
    <div class="muted small">Overrides nothing by itself — it is added on top of the built-in prompt. Clear the box to remove it.</div></div></div>
  <p class="muted">Data folder: ${esc(S.state.data_dir)}</p>
  <div class="hstack" style="justify-content:flex-end"><button data-close="1">Cancel</button><button class="primary" id="s_save">Save</button></div>`);
  const loadModels = async (keep) => {
    $("#s_msg").textContent = "Loading models…";
    const r = await api("/api/models", { method: "POST", body: { base_url: $("#s_base").value, api_key: $("#s_key").value } });
    const cur = keep || $("#s_model").value;
    const list = r.models || []; const missing = cur && !list.includes(cur);
    // 1.5 fix: never switch the model silently - keep the configured one selected even if this server doesn't list it
    $("#s_model").innerHTML = (missing ? [`<option value="${esc(cur)}" selected>${esc(cur)} (not found on this server)</option>`] : [])
      .concat(list.map(m => `<option value="${esc(m)}" ${m === cur ? "selected" : ""}>${esc(m)}</option>`)).join("");
    $("#s_msg").textContent = (r.ok ? `${list.length} model(s) found` : "Couldn't list models: " + r.error) + (missing ? ` - "${cur}" is not on this server; it stays selected until you pick another.` : "");
  };
  $("#s_provider").onchange = () => { const p = P[$("#s_provider").value]; if (p.base_url) $("#s_base").value = p.base_url; loadModels(); };
  $("#s_refresh").onclick = () => loadModels();
  api("/api/fallback").then(f => { const el = $("#s_fbst"); if (!el) return;
    el.textContent = !f.enabled ? "Backup model is off." : f.error ? "Could not list installed models: " + f.error
      : !f.same_server ? "Backup only works with local Ollama models (your selected server is different)."
      : f.effective ? "Backup if needed: " + f.effective + " (installed). Your selected model stays first." : "No other suitable installed model, so there is no backup. Nothing will be downloaded."; }).catch(() => {});
  $("#s_quick").querySelectorAll("[data-quick]").forEach(b => b.onclick = async () => { if (await quickSwitch(b.dataset.quick)) { closeModal(); } });
  $("#s_updates").onclick = () => openUpdates();
  $("#s_wizard").onclick = () => openWizard(); $("#s_models").onclick = () => openModels();
  $("#s_temp").oninput = () => $("#s_tv").textContent = $("#s_temp").value;
  $("#s_detect").onclick = async () => {
    $("#s_msg").textContent = "Scanning local ports…"; const r = await api("/api/autodetect");
    $("#s_msg").textContent = r.found.length ? `Found ${r.found.length} server(s)` : "No local AI servers found. Is Ollama / LM Studio running?";
    $("#s_found").innerHTML = r.found.map((f, i) => `<div class="chip">✅ ${esc(f.label)} @ ${esc(f.base_url)} — ${f.models.length} models <button data-use="${i}" style="color:var(--accent)">Use</button></div>`).join("");
    $("#s_found").querySelectorAll("[data-use]").forEach(b => b.onclick = () => { const f = r.found[b.dataset.use]; $("#s_provider").value = f.provider; $("#s_base").value = f.base_url;
      $("#s_model").innerHTML = f.models.map(m => `<option>${esc(m)}</option>`).join(""); });
  };
  $("#s_test").onclick = async () => { $("#s_msg").textContent = "Testing…"; const r = await api("/api/test", { method: "POST", body: { base_url: $("#s_base").value, api_key: $("#s_key").value, model: $("#s_model").value } });
    $("#s_msg").textContent = r.ok ? "✅ Connected — model replied: " + r.reply : "❌ " + r.error; };
  $("#s_autoall").onchange = () => $("#s_autowarn").classList.toggle("hidden", !$("#s_autoall").checked);
  $("#s_aa").querySelectorAll("[data-rmaa]").forEach(b => b.onclick = () => b.parentNode.remove());
  $("#s_save").onclick = async () => {
    await api("/api/settings", { method: "POST", body: { provider: $("#s_provider").value, base_url: $("#s_base").value, api_key: $("#s_key").value, model: $("#s_model").value,
      temperature: +$("#s_temp").value, native_tools: $("#s_native").checked, auto_reads: $("#s_reads").checked, auto_approve_all: $("#s_autoall").checked,
      think_mode: $("#s_think").value, quality_guard: $("#s_quality").checked, refusal_guard: $("#s_quality").checked, clarity_nudge: $("#s_clarify").checked, prompt_improve: $("#s_pimpr").checked, polish_english: $("#s_polish") ? $("#s_polish").checked : true, tips_dismissed: !$("#s_tips").checked, user_name: $("#s_user").value, theme: $("#s_theme").value,
      custom_system_prompt: $("#s_custom").value, fallback_enabled: $("#s_fb").checked, fallback_models: $("#s_fbm").value, searx_url: ($("#s_searx") && $("#s_searx").value || "").trim(), always_allow: [...document.querySelectorAll("#s_aa .chip")].map(c => c.firstChild.textContent) } });
    closeModal(); await refresh(); if (!S.convId) emptyState();
  };
  if (c.base_url) loadModels(c.model).catch(() => { });
}
function openAgent(id) {
  const a = id ? S.state.agents.find(x => x.id === id) : { name: "", persona: "You are {name}, a specialist agent. ", model: "", tools: ["*"], memory: "" };
  const all = a.tools.includes("*");
  modal(`<h2>${id ? "Edit agent" : "New agent"}</h2>
  <div class="row"><label>Name</label><input id="a_name" value="${esc(a.name)}"></div>
  <div class="row"><label>Persona / system prompt</label><textarea class="f" id="a_persona" rows="9">${esc(a.persona)}</textarea></div>
  <div class="row"><label>Model</label><input id="a_model" placeholder="(default: ${esc(S.state.config.model || "settings model")})" value="${esc(a.model)}"></div>
  <div class="row"><label>Allowed tools</label><div><label class="hstack"><input type="checkbox" id="a_all" style="width:auto" ${all ? "checked" : ""}> All tools</label>
   <div class="toolgrid" id="a_tools">${S.state.tools.map(t => `<label class="hstack"><input type="checkbox" style="width:auto" value="${t.name}" ${all || a.tools.includes(t.name) ? "checked" : ""}> ${t.name}${t.changes ? " 🔐" : ""}</label>`).join("")}</div></div></div>
  <div class="row"><label>Memory notes</label><textarea class="f" id="a_mem" rows="4">${esc(a.memory || "")}</textarea></div>
  <div class="hstack" style="justify-content:space-between">${id && !a.is_main ? '<button class="danger" id="a_del">Delete agent</button>' : "<span></span>"}<div class="hstack"><button data-close="1">Cancel</button><button class="primary" id="a_save">Save</button></div></div>`);
  $("#a_save").onclick = async () => {
    const tools = $("#a_all").checked ? ["*"] : [...document.querySelectorAll("#a_tools input:checked")].map(x => x.value);
    const body = { name: $("#a_name").value.trim(), persona: $("#a_persona").value, model: $("#a_model").value.trim(), tools, memory: $("#a_mem").value };
    try { const r = id ? await api("/api/agents/" + id, { method: "PUT", body }) : await api("/api/agents", { method: "POST", body }); S.agentId = r.id; closeModal(); await refresh(); openConv(null); }
    catch (e) { alert(e.message); }
  };
  if ($("#a_del")) $("#a_del").onclick = async () => { if (!confirm(`Delete ${a.name} and its conversations?`)) return; await api("/api/agents/" + id, { method: "DELETE" }); S.agentId = null; closeModal(); await refresh(); openConv(null); };
}
async function openAudit() {
  const rows = await api("/api/audit");
  modal(`<h2>Audit log</h2><table class="audit"><tr><th>Time</th><th>Agent</th><th>Tool</th><th>Arguments</th><th>Decision</th></tr>${rows.map(r => `<tr><td>${new Date(r.ts * 1000).toLocaleString()}</td><td>${esc(r.agent)}</td><td>${esc(r.tool)}</td><td><code>${esc(r.args.slice(0, 300))}</code></td><td>${esc(r.decision)}</td></tr>`).join("") || "<tr><td colspan=5 class=muted>No actions yet</td></tr>"}</table><div class="hstack" style="justify-content:flex-end;margin-top:12px"><button data-close="1">Close</button></div>`);
}

// ---------- events ----------
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 220) + "px"; }
$("#input").addEventListener("input", autosize);
$("#input").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("#input").value); } });
$("#composer").onsubmit = e => { e.preventDefault(); send($("#input").value); };
$("#stop").onclick = () => S.runId && api("/api/stop/" + S.runId, { method: "POST" });
$("#newChat").onclick = () => openConv(null);
$("#addAgent").onclick = () => openAgent(null);
$("#openUpdates").onclick = () => openUpdates(); $("#updNotice").onclick = () => openUpdates();
$("#openSettings").onclick = openSettings; $("#openModels").onclick = () => openModels(); $("#openAudit").onclick = openAudit;
$("#toggleTheme").onclick = async () => { await api("/api/settings", { method: "POST", body: { theme: S.state.config.theme === "light" ? "dark" : "light" } }); refresh(); };
document.addEventListener("click", e => {
  const t = e.target;
  if (t.classList.contains("copy")) { navigator.clipboard.writeText(t.nextElementSibling.textContent); t.textContent = "Copied"; setTimeout(() => t.textContent = "Copy", 1200); return; }
  if (t.dataset.close) return closeModal();
  if (t.dataset.opensettings) return openSettings();
  if (t.dataset.wizard) return openWizard();
  if (t.dataset.sugg) return send(t.dataset.sugg);
  if (t.dataset.regen) { const lu = [...document.querySelectorAll("#messages > .msg.user")].pop(); if (lu) removeAfter(lu, false); return send("", { regenerate: true }); }
  if (t.dataset.editlast) return startEdit(t.closest(".msg.user"));
  if (t.dataset.reconnect) { $("#connBanner").innerHTML = "Checking…"; return checkConn(); }
  if (t.dataset.startollama) return (async () => { const host = document.createElement("div"); $("#connBanner").append(host);
    if (!await askPermission(host, "Start the Ollama service", "ollama serve", "start_ollama", {})) return;
    $("#connBanner").innerHTML = "Starting Ollama…"; try { const r = await api("/api/ollama/start", { method: "POST", body: { approved: true } }); if (!r.ok) alert(r.message); } catch (e) { alert(e.message); } checkConn(); })();
  if (t.dataset.openmodels) return openModels();
  if (t.dataset.closebanner) { $("#connBanner").className = "hidden"; return; }
  if (t.dataset.tryadult) { api("/api/settings", { method: "POST", body: { adult_model_suggested: true } }).then(() => quickSwitch("huihui_ai/qwen2.5-abliterate:3b").then(refresh)); return; }
  if (t.dataset.dismissadult) { api("/api/settings", { method: "POST", body: { adult_model_suggested: true } }).then(refresh); $("#connBanner").className = "hidden"; return; }
  if (t.dataset.closetips) { api("/api/settings", { method: "POST", body: { tips_dismissed: true } }).then(refresh); $("#tips").classList.add("hidden"); return; }
  if (t.dataset.quick && !t.closest("#modal")) return quickSwitch(t.dataset.quick);
  if (t.dataset.edit) { e.stopPropagation(); return openAgent(t.dataset.edit); }
  if (t.dataset.ren) { const n = prompt("Rename conversation:"); if (n) api("/api/conversations/" + t.dataset.ren, { method: "PUT", body: { title: n } }).then(refresh); return; }
  if (t.dataset.delc) { if (confirm("Delete this conversation?")) api("/api/conversations/" + t.dataset.delc, { method: "DELETE" }).then(async () => { if (S.convId === t.dataset.delc) S.convId = null; await refresh(); openConv(S.convId); }); return; }
  const ag = t.closest("[data-agent]"); if (ag && !S.busy) { S.agentId = ag.dataset.agent; S.convId = null; renderSidebar(); renderTop(); return openConv(null); }
  const cv = t.closest("[data-conv]"); if (cv && !S.busy) return openConv(cv.dataset.conv);
});
refresh().then(() => { emptyState(); if (!S.state.config.setup_done && !S.state.config.model) openWizard(); checkConn(); });
