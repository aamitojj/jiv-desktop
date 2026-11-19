// Updates page + self-update chat card
const fmtTime = t => new Date(t * 1000).toLocaleString();

function updateCard(host, meta, opts = {}) {
  const m = meta.manifest || {};
  const box = document.createElement("div"); box.className = "upd-card"; box.dataset.id = meta.id;
  const diff = meta.diff || [];
  const sigTag = !meta.ok ? `<span class="tag bad">✗ Invalid</span>` : meta.signed ? `<span class="tag good">🔏 ${esc(meta.label)}</span>` : `<span class="tag warn">⚠ ${esc(meta.label || "Unsigned – created locally")}</span>`;
  const origin = { self: "written by Jiv", upload: "added from a file", github: "from GitHub" }[meta.origin] || meta.origin;
  box.innerHTML = `<div class="upd-head"><div><b>${esc(meta.title || m.name || "Update")}</b> <span class="badge">v${esc(m.version || "?")}</span> ${sigTag} <span class="muted small">${esc(origin)}${meta.applied ? " · applied" : ""}</span></div></div>
    ${meta.ok ? "" : `<div class="err">${esc(meta.error)}</div>`}
    ${m.changelog ? `<div class="md upd-log">${md(m.changelog)}</div>` : ""}
    ${(meta.checks || []).length ? `<div class="upd-checks">${meta.checks.map(c => `<details class="chk ${c.ok ? "ok" : "bad"}"><summary>${c.ok ? "✅" : "❌"} ${esc(c.name)}${c.seconds ? ` <span class="muted">(${c.seconds}s)</span>` : ""}</summary>${c.output ? `<pre>${esc(c.output)}</pre>` : ""}</details>`).join("")}</div>` : ""}
    ${(m.requirements || []).length ? `<div class="upd-reqs">📦 Also installs Python packages into Jiv's own Python: ${m.requirements.map(r => `<code>${esc(r)}</code>`).join(" ")}</div>` : ""}
    ${diff.length ? `<div class="upd-files"><div class="muted small">${diff.length} file(s):</div>${diff.map(f => `<details class="fdiff"><summary><span class="act ${f.action}">${f.action}</span> <code>${esc(f.path)}</code> <span class="plus">+${f.added}</span> <span class="minus">−${f.removed}</span>${f.core ? ' <span class="tag warn">core</span>' : ""}</summary><pre class="diff">${colorDiff(f.diff)}</pre></details>`).join("")}</div>` : ""}
    ${meta.touches_core && meta.touches_core.length && !meta.signed ? `<label class="hstack adv"><input type="checkbox" class="advanced" style="width:auto"> Advanced: allow this unsigned update to change the updater / permission core (${esc(meta.touches_core.join(", "))})</label>` : ""}
    ${meta.origin === "self" && meta.checks_ok === false ? `<label class="hstack adv"><input type="checkbox" class="advanced" style="width:auto"> Advanced: apply even though checks failed</label>` : ""}
    <div class="upd-perm"></div>
    <div class="hstack upd-btns">${meta.ok && !meta.applied ? '<button class="primary" data-upd-apply>Apply update…</button>' : ""}${opts.inChat ? '<button data-upd-open>Open in Updates</button>' : ""}${!meta.applied ? '<button class="ghost" data-upd-discard>Discard</button>' : ""}</div>`;
  host.append(box);
  const btn = box.querySelector("[data-upd-apply]");
  if (btn) btn.onclick = async () => {
    const adv = box.querySelector(".advanced")?.checked || false;
    const exact = `Install Jiv ${m.version} (${meta.signed ? "signed" : "UNSIGNED, created locally"})\n` +
      `• back up the app folder to <data>/backups first\n${(m.requirements || []).length ? "• pip install " + m.requirements.join(" ") + " into Jiv's Python\n" : ""}• ${diff.map(f => f.action + " " + f.path).join("\n• ")}\n• restart Jiv; auto-rollback if it isn't healthy within 30s\n(your settings, chats, agents and models are not touched)`;
    const ok = await askPermission(box.querySelector(".upd-perm"), "Apply this update to Jiv", exact, "apply_update", { id: meta.id });
    if (!ok) { api("/api/updates/deny/" + meta.id, { method: "POST" }); return; }
    try {
      const r = await api("/api/updates/apply/" + meta.id, { method: "POST", body: { approved: true, advanced: adv } });
      if (r.restarting) restartingOverlay(r.version); else { alert(r.message); openUpdates(); }
    } catch (e) { box.querySelector(".upd-perm").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
  box.querySelector("[data-upd-discard]")?.addEventListener("click", async () => { await api("/api/updates/discard/" + meta.id, { method: "POST" }); box.remove(); });
  box.querySelector("[data-upd-open]")?.addEventListener("click", () => openUpdates(meta.id));
  return box;
}
function colorDiff(t) {
  return esc(t || "").split("\n").map(l => l.startsWith("+") && !l.startsWith("+++") ? `<span class="plus">${l}</span>` : l.startsWith("-") && !l.startsWith("---") ? `<span class="minus">${l}</span>` : l.startsWith("@@") ? `<span class="hunk">${l}</span>` : l).join("\n");
}
function restartingOverlay(version) {
  modal(`<div class="center"><div class="spinner"></div><h2>Updating to ${esc(version)}…</h2><p class="muted">Jiv is restarting. If the new version doesn't start properly within 30 seconds it is rolled back automatically.</p><p id="rsMsg" class="muted"></p></div>`);
  const t0 = Date.now(); let wentDown = false;
  const tick = async () => {
    try {
      const r = await fetch("/health", { cache: "no-store" }); const h = await r.json();
      if (h.version === version || (wentDown && Date.now() - t0 > 4000)) { location.reload(); return; }
    } catch { wentDown = true; $("#rsMsg") && ($("#rsMsg").textContent = "Waiting for Jiv to come back…"); }
    if (Date.now() - t0 < 90000) setTimeout(tick, 1000); else $("#rsMsg").textContent = "Jiv didn't come back. Start it again; your previous version is in Backups.";
  };
  setTimeout(tick, 1500);
}
function installerCard(inst) {
  $("#uInst")?.remove();
  const box = document.createElement("div"); box.className = "upd-card"; box.id = "uInst";
  box.innerHTML = `<div class="upd-head"><b>Full installer for ${esc(inst.version)}</b> <span class="badge">${esc(inst.name)}</span> <span class="muted small">${inst.size ? (inst.size / 1048576).toFixed(1) + " MB" : ""}</span></div>
    <div class="muted">Big updates (for example a new built-in Python) come as a new JivSetup.exe. It installs over this one and keeps your chats, agents and settings.</div>
    <div class="upd-perm"></div><div class="hstack upd-btns"><button class="primary" data-inst>Download & run installer…</button></div>`;
  $("#uStaged").prepend(box);
  box.querySelector("[data-inst]").onclick = async () => {
    const ok = await askPermission(box.querySelector(".upd-perm"), "Run the Jiv installer", `Download ${inst.name} from\n${inst.url}\nback up the app folder, close Jiv and run the installer (Windows may show a SmartScreen warning: More info → Run anyway).`, "run_installer", inst);
    if (!ok) return;
    try { const r = await api("/api/updates/installer", { method: "POST", body: { approved: true } }); box.querySelector(".upd-perm").innerHTML = `<div class="muted">${esc(r.message)}</div>`; }
    catch (e) { box.querySelector(".upd-perm").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
  return box;
}

async function openUpdates(focusId) {
  const u = await api("/api/updates");
  const st = u.state || {};
  const stateMsg = st.status === "rolled_back" ? `<div class="err">↩ ${esc(st.message)}</div>` : st.status === "ok" && st.message ? `<div class="notice ok-note">✅ ${esc(st.message)}</div>` : st.status === "failed" ? `<div class="err">${esc(st.message)}</div>` : "";
  modal(`<div class="wiz-head"><h2>⬆ Updates</h2><button class="icon" data-close="1">✕</button></div>
   <div class="statusline">Installed version <b>${esc(u.version)}</b>${u.layout && u.layout.installed ? ` <span class="muted small">· installed app in ${esc(u.layout.root)}</span>` : ""}${u.frozen ? ' <span class="tag warn">packaged .exe/.app – updates need a rebuild</span>' : ""}${!u.restart_supported && !u.frozen ? ' <span class="muted small">(restart manually after updating)</span>' : ""}</div>
   ${stateMsg}${u.notice && u.notice.message ? `<div class="notice">🔔 ${esc(u.notice.message)}</div>` : ""}
   <h3>Get updates</h3>
   <div class="hstack"><button class="primary" id="uCheck">🔄 Check for updates</button>
     <label class="filebtn"><input type="file" id="uFile" accept=".jivupdate,.zip" hidden><span class="btnlike">📦 Install update file…</span></label><span id="uMsg" class="muted"></span></div>
   <details class="usrc"><summary>Source: <code>${esc(u.repo)}</code>${u.repo_is_default ? ' <span class="muted small">(official, default)</span>' : ""}</summary>
     <div class="row"><label>GitHub repo</label><input id="uRepo" placeholder="${esc(u.default_repo || "owner/repo")} (leave empty for the official repo)" value="${esc(u.repo_is_default ? "" : (u.repo || ""))}"></div>
     <div class="row"><label>Automatic</label><label class="hstack"><input type="checkbox" id="uAuto" style="width:auto" ${u.auto_install_signed ? "checked" : ""}> Auto-install <b>signed</b> updates from my GitHub (notifies you and restarts only when Jiv is idle)</label></div>
     <div class="hstack" style="justify-content:flex-end"><button id="uSaveSrc">Save source</button></div></details>
   <h3>Ready to install</h3><div id="uStaged">${u.staged.filter(s => !s.applied).length ? "" : '<div class="muted">Nothing waiting. Check for updates, add a file, or ask Jiv to write one ("add a feature that…").</div>'}</div>
   <h3>Backups</h3>
   <div id="uPerm"></div>
   <table class="audit"><tr><th>Version</th><th>Created</th><th>Reason</th><th></th></tr>${u.backups.map((b, i) => `<tr><td><b>${esc(b.version)}</b></td><td>${fmtTime(b.created)}</td><td>${esc(b.reason)}</td><td><button data-rb="${esc(b.name)}" data-rbv="${esc(b.version)}" ${i === 0 ? 'class="primary"' : ""}>${i === 0 ? "↩ Roll back to previous version" : "Restore"}</button></td></tr>`).join("") || '<tr><td colspan=4 class="muted">No backups yet — one is made automatically before every update.</td></tr>'}</table>`);
  $("#modalCard").classList.add("wide");
  const host = $("#uStaged");
  for (const s of u.staged.filter(s => !s.applied)) { const full = await api("/api/updates/staged/" + s.id).catch(() => s); const c = updateCard(host, full); if (s.id === focusId) c.scrollIntoView(); }
  $("#uCheck").onclick = async () => {
    $("#uMsg").textContent = "Checking…"; const r = await api("/api/updates/check", { method: "POST" });
    $("#uMsg").textContent = r.error || r.message || (r.available ? `Version ${r.latest} downloaded — review it below.` : "");
    if (r.staged) { host.querySelector(".muted")?.remove(); updateCard(host, r.staged).scrollIntoView(); }
    if (r.installer && r.available) installerCard(r.installer);
  };
  if (u.installer && u.installer.url && u.installer.version && u.installer.version !== u.version) installerCard(u.installer);
  $("#uFile").onchange = async e => {
    const f = e.target.files[0]; if (!f) return; $("#uMsg").textContent = "Verifying " + f.name + "…";
    const r = await fetch("/api/updates/upload", { method: "POST", headers: { "X-Jiv-Token": TOKEN, "Content-Type": "application/octet-stream", "X-Filename": f.name }, body: f });
    const meta = await r.json(); if (!r.ok) { $("#uMsg").textContent = meta.detail || "Upload failed"; return; }
    $("#uMsg").textContent = meta.ok ? "Verified ✓ — review and apply below." : "This file can't be installed.";
    host.querySelector(".muted")?.remove(); updateCard(host, meta).scrollIntoView();
  };
  $("#uSaveSrc").onclick = async () => {
    try { await api("/api/updates/settings", { method: "POST", body: { update_repo: $("#uRepo").value, auto_install_signed: $("#uAuto").checked } }); openUpdates(); }
    catch (e) { alert(e.message); }
  };
  document.querySelectorAll("[data-rb]").forEach(b => b.onclick = async () => {
    const ok = await askPermission($("#uPerm"), "Roll back Jiv", `Restore version ${b.dataset.rbv} from backup ${b.dataset.rb}\n(the current version is backed up first; settings, chats and models are untouched)\nthen restart Jiv`, "rollback", { backup: b.dataset.rb });
    if (!ok) return;
    try { const r = await api("/api/updates/rollback", { method: "POST", body: { approved: true, backup: b.dataset.rb } }); if (r.restarting) restartingOverlay(r.version); else { alert("Rolled back. Restart Jiv to finish."); openUpdates(); } }
    catch (e) { alert(e.message); }
  });
}
