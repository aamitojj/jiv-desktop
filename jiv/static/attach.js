// Chat attachments: 📎 button, drag & drop, paste. Files upload right away; chips show them until you send.
const ATT = { list: [] };  // {key, name, size, status: uploading|ready|error, meta, error}
const KIND_ICON = { pdf: "📕", docx: "📘", xlsx: "📗", pptx: "📙", image: "🖼", text: "📄", binary: "📦" };
function fmtSize(n) { return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(0) + " KB" : (n / 1048576).toFixed(1) + " MB"; }
function rawUrl(id) { return `/api/uploads/${id}/raw?t=${encodeURIComponent(TOKEN)}`; }
function chipHtml(a, removable) {
  const m = a.meta || a; const kind = m.kind || "binary";
  const thumb = kind === "image" && m.id ? `<img src="${rawUrl(m.id)}" alt="">` : `<span class="ic">${KIND_ICON[kind] || "📄"}</span>`;
  const sub = a.status === "uploading" ? "uploading…" : a.status === "error" ? esc(a.error || "failed") :
    m.error ? "⚠ " + esc(m.error) : kind === "image" ? (m.vision === false ? "image · model can't see images" : "image") :
    `${fmtSize(m.size || 0)}${m.chars != null ? " · " + Number(m.chars).toLocaleString() + " chars" : ""}`;
  return `<div class="chip-att ${a.status || "ready"} ${m.error ? "warn" : ""}" title="${esc(m.name || a.name)}">${thumb}<div class="ct"><b>${esc(m.name || a.name)}</b><small>${sub}</small></div>${removable ? `<button type="button" class="x" data-rmatt="${a.key}" title="Remove">✕</button>` : ""}</div>`;
}
function renderChips() {
  const box = $("#attachChips"); box.classList.toggle("hidden", !ATT.list.length);
  box.innerHTML = ATT.list.map(a => chipHtml(a, true)).join("");
  const vis = ATT.list.find(a => a.meta && a.meta.kind === "image" && a.meta.vision === false);
  if (vis) box.insertAdjacentHTML("beforeend", `<div class="att-tip">🖼 The current model can't see images. Install a vision model such as <b>qwen2.5vl:3b</b> or <b>llava:7b</b> in 📦 Models, then pick it in Settings.</div>`);
}
async function addFiles(files) {
  for (const f of files) {
    if (ATT.list.length >= 10) { alert("Up to 10 files per message."); break; }
    const a = { key: Math.random().toString(36).slice(2), name: f.name || "pasted-file", size: f.size, status: "uploading" };
    ATT.list.push(a); renderChips();
    try {
      const r = await fetch("/api/uploads", { method: "POST", headers: { "X-Jiv-Token": TOKEN, "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(a.name) }, body: f });
      const j = await r.json(); if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
      a.meta = j; a.status = "ready";
    } catch (e) { a.status = "error"; a.error = e.message; }
    renderChips();
  }
}
function takeAttachments() {  // ids to send + snapshot for the bubble; clears the chips
  const ready = ATT.list.filter(a => a.status === "ready"); ATT.list = []; renderChips();
  return ready.map(a => a.meta);
}
function attachmentsBusy() { return ATT.list.some(a => a.status === "uploading"); }
function attachmentsHtml(metas) {
  if (!metas || !metas.length) return "";
  return `<div class="msg-atts">${metas.map(m => m.kind === "image" && m.id ? `<a class="img-att" href="${rawUrl(m.id)}" target="_blank" rel="noopener"><img src="${rawUrl(m.id)}" alt="${esc(m.name)}"></a>` : chipHtml({ meta: m, status: "ready" }, false)).join("")}</div>`;
}
document.addEventListener("click", e => { const k = e.target.dataset && e.target.dataset.rmatt; if (k) { ATT.list = ATT.list.filter(a => a.key !== k); renderChips(); } });
document.addEventListener("DOMContentLoaded", () => {
  $("#attachBtn").onclick = () => $("#attachInput").click();
  $("#attachInput").onchange = e => { addFiles([...e.target.files]); e.target.value = ""; };
  let depth = 0; const dz = $("#dropZone");
  const hasFiles = e => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", e => { if (!hasFiles(e)) return; depth++; dz.classList.remove("hidden"); e.preventDefault(); });
  window.addEventListener("dragover", e => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; dz.classList.add("hidden"); } });
  window.addEventListener("drop", e => { if (!hasFiles(e)) return; e.preventDefault(); depth = 0; dz.classList.add("hidden"); addFiles([...e.dataTransfer.files]); });
  $("#input").addEventListener("paste", e => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); addFiles(files.map((f, i) => f.name && f.name !== "image.png" ? f : new File([f], `pasted-${Date.now()}-${i}.${(f.type.split("/")[1] || "png").replace("jpeg", "jpg")}`, { type: f.type }))); }
  });
});
