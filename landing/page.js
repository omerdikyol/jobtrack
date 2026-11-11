// Page behavior: reveal on scroll, the screenshot tabs and the copy buttons. Everything degrades to a plain page without it.
"use strict";
(() => {

  const io = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { threshold: 0.15 });
  document.querySelectorAll(".reveal").forEach((n) => io.observe(n));

  const shot = document.getElementById("shot"), tabs = document.querySelectorAll(".tabs button");
  tabs.forEach((tab) => tab.addEventListener("click", () => {
    tabs.forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    shot.classList.add("swap");
    setTimeout(() => {
      shot.src = tab.dataset.shot; shot.width = +tab.dataset.w; shot.height = +tab.dataset.h;
      shot.onload = () => shot.classList.remove("swap");
    }, 220);
  }));

  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; }
    catch { b.textContent = "Select and copy"; }
    setTimeout(() => (b.textContent = "Copy"), 1500);
  }));
})();
