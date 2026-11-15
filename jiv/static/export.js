// Jiv 1.2.1 feature: export the current chat as a Markdown file (button in the top bar, or Ctrl+Shift+E)
async function exportChat() {
  if (!S.convId) { alert("Open a chat first."); return; }
  const d = await api("/api/conversations/" + S.convId);
  const title = (d.conversation && d.conversation.title) || "Jiv chat";
  const lines = [`# ${title}`, "", `_Exported from Jiv Desktop on ${new Date().toLocaleString()}_`, ""];
  for (const m of d.messages) {
    if (m.role === "user") lines.push("## 🧑 You", "", m.content, "");
    else if (m.role === "assistant" && m.content) lines.push(`## 🤖 ${(m.meta && m.meta.agent) || "Jiv"}`, "", m.content, "");
    else if (m.role === "tool") lines.push("<details><summary>Tool result</summary>", "", "```", String(m.content).slice(0, 2000), "```", "</details>", "");
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = title.replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "_").slice(0, 60) + ".md";
  document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
document.getElementById("exportChat").addEventListener("click", exportChat);
document.addEventListener("keydown", e => { if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "e") { e.preventDefault(); exportChat(); } });
