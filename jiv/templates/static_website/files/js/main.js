// {{ project_name }}
document.addEventListener("DOMContentLoaded", () => {
  const f = document.getElementById("contact-form");
  if (f) f.addEventListener("submit", (e) => {
    e.preventDefault();
    const n = document.getElementById("form-note");
    if (n) n.textContent = "Thanks — this demo form does not send data anywhere.";
  });
});
