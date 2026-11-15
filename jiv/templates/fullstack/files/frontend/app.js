const API = "http://127.0.0.1:{{ port }}";
const statusEl = () => document.getElementById("status");
async function load() {
  try {
    const r = await fetch(API + "/items");
    const items = await r.json();
    const ul = document.getElementById("list");
    ul.innerHTML = "";
    (items || []).forEach(it => {
      const li = document.createElement("li");
      li.textContent = (it.name || JSON.stringify(it));
      ul.appendChild(li);
    });
    statusEl().textContent = "Connected to " + API;
  } catch (e) {
    statusEl().textContent = "Cannot reach the API. Start the Java backend (Builder → Run) and keep this page open.";
  }
}
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = e.target.name.value.trim();
  if (!name) return;
  await fetch(API + "/items", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
  e.target.reset();
  load();
});
load();
