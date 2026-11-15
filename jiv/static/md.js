// Tiny safe markdown renderer (escape first, then format). No external deps.
window.md = (function () {
  const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  function inline(s) {
    const codes = [];
    s = s.replace(/`([^`\n]+)`/g, (_, c) => (codes.push(c), `\u0000${codes.length - 1}\u0000`));
    s = esc(s)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/(^|\W)_([^_\n]+)_(?=\W|$)/g, "$1<em>$2</em>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
  }
  return function render(src) {
    const out = [], lines = (src || "").replace(/\r/g, "").split("\n");
    let i = 0, para = [];
    const flush = () => { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } };
    while (i < lines.length) {
      const l = lines[i];
      const fence = l.match(/^\s*```\s*([\w+-]*)/);
      if (fence) {
        flush(); const buf = []; i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
        i++;
        out.push(`<pre><button class="copy">Copy</button><code class="lang-${esc(fence[1])}">${esc(buf.join("\n"))}</code></pre>`);
        continue;
      }
      let m;
      if ((m = l.match(/^(#{1,6})\s+(.*)/))) { flush(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++; continue; }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
        flush(); const ordered = /^\s*\d/.test(l); const items = [];
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, ""));
        out.push(`<${ordered ? "ol" : "ul"}>${items.map(x => "<li>" + inline(x) + "</li>").join("")}</${ordered ? "ol" : "ul"}>`); continue;
      }
      if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|[\s:-|]+\|\s*$/.test(lines[i + 1])) {
        flush(); const row = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => inline(c.trim()));
        const head = row(l); i += 2; const rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(row(lines[i++]));
        out.push("<table><tr>" + head.map(h => "<th>" + h + "</th>").join("") + "</tr>" + rows.map(r => "<tr>" + r.map(c => "<td>" + c + "</td>").join("") + "</tr>").join("") + "</table>"); continue;
      }
      if (/^>\s?/.test(l)) { flush(); out.push("<blockquote>" + inline(l.replace(/^>\s?/, "")) + "</blockquote>"); i++; continue; }
      if (!l.trim()) { flush(); i++; continue; }
      para.push(l); i++;
    }
    flush(); return out.join("");
  };
})();
