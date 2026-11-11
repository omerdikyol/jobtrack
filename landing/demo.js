// The live demo: a scripted replay of one sync, in a working copy of jobtrack's layout. Mail lands
// in the inbox, a scanner sweeps it, a postmark names its stage, and the pipeline, the application
// list and the timeline update. Hover to pause; click an application to open its timeline.
// Reduced motion renders the finished state once. All companies are invented.
"use strict";
(() => {
  const root = document.getElementById("demo");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const LABEL = { applied: "Applied", assessment: "Assessment", interview: "Interviewing", offer: "Offer", rejected: "Rejected" };
  const STAMP = { applied: "Applied", assessment: "Task", interview: "Interview", offer: "Offer", rejected: "Rejected" };
  const STAGES = Object.keys(LABEL);
  const SCRIPT = [
    ["Nordvale Careers", "Thanks for applying: Backend Engineer", "Nordvale", "Backend Engineer", "applied", "Sep 3"],
    ["Brightline Talent", "We received your application", "Brightline", "Data Analyst", "applied", "Sep 4"],
    ["Quorix Recruiting", "Application received: Platform Engineer", "Quorix", "Platform Engineer", "applied", "Sep 5"],
    ["Nordvale Careers", "Your coding challenge at Nordvale", "Nordvale", "Backend Engineer", "assessment", "Sep 9"],
    ["Brightline Talent", "An update on your application", "Brightline", "Data Analyst", "rejected", "Sep 12"],
    ["Quorix Recruiting", "Can we speak on Thursday?", "Quorix", "Platform Engineer", "interview", "Sep 14"],
    ["Larkspur Labs", "Thank you for your interest in Larkspur", "Larkspur", "Frontend Developer", "applied", "Sep 15"],
    ["Nordvale Careers", "Interview invitation: Backend Engineer", "Nordvale", "Backend Engineer", "interview", "Sep 18"],
    ["Quorix Recruiting", "Offer: Platform Engineer", "Quorix", "Platform Engineer", "offer", "Sep 24"],
    ["Larkspur Labs", "Unfortunately, we won't be moving forward", "Larkspur", "Frontend Developer", "rejected", "Sep 26"],
  ];

  let apps, shown, run = 0, paused = false, visible = false, pickedAt = 0, selected = null;

  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function sleep(ms, my) {
    return new Promise((resolve) => {
      let t = 0;
      const id = setInterval(() => {
        if (my !== run) { clearInterval(id); resolve(false); return; }
        if (!paused && visible) { t += 50; if (t >= ms) { clearInterval(id); resolve(true); } }
      }, 50);
    });
  }

  function tween(node, to, suffix = "") {
    const from = parseInt(node.textContent, 10) || 0;
    if (reduce || from === to) { node.innerHTML = to + suffix; return; }
    const t0 = performance.now();
    const tick = (now) => {
      const k = Math.min(1, (now - t0) / 600);
      node.innerHTML = Math.round(from + (to - from) * k) + suffix;
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function counts() {
    const c = Object.fromEntries(STAGES.map((s) => [s, 0]));
    for (const a of apps.values()) c[a.status]++;
    const total = apps.size, replied = total - c.applied;
    return { c, total, rate: total ? Math.round((replied / total) * 100) : 0 };
  }

  function paint() {
    const { c, total, rate } = counts();
    tween($("t-apps"), total); tween($("t-int"), c.interview); tween($("t-off"), c.offer); tween($("t-rate"), rate, "<i>%</i>");
    for (const s of STAGES) {
      tween($("l-" + s), c[s]);
      root.querySelector(".seg .s-" + s).style.width = total ? (c[s] / total) * 100 + "%" : "0";
    }
  }

  function chip(status) { const c = el("span", "chip s-" + status, LABEL[status]); return c; }

  function renderTimeline() {
    const list = $("tl"); list.innerHTML = "";
    const a = selected && apps.get(selected);
    $("tl-title").textContent = a ? a.company + " · " + a.role : "Timeline";
    if (!a) return;
    for (const e of a.tl) {
      const li = el("li", "s-" + e.kind, `<b>${LABEL[e.kind]}</b> <small>${esc(e.date)} · ${esc(e.subject)}</small>`);
      li.style.setProperty("--c", `var(--${e.kind})`);
      list.appendChild(li);
    }
  }

  function select(company, byUser) {
    selected = company; if (byUser) pickedAt = Date.now();
    for (const r of $("rows").children) r.classList.toggle("sel", r.dataset.co === company);
    renderTimeline();
  }

  function applyEvent(ev) {
    const [, subject, company, role, kind, date] = ev;
    let a = apps.get(company), row = $("rows").querySelector(`[data-co="${company}"]`);
    if (!a) {
      a = { company, role, status: kind, tl: [] }; apps.set(company, a);
      row = el("li", "row", `<span class="av">${company[0]}</span><span class="who"><b>${company}</b><span>${role}</span></span>`);
      row.dataset.co = company; row.tabIndex = 0;
      row.addEventListener("click", () => select(company, true));
      row.appendChild(chip(kind));
      $("rows").prepend(row);
    } else {
      a.status = kind;
      const old = row.querySelector(".chip"); old.className = "chip s-" + kind; old.textContent = LABEL[kind];
    }
    a.tl.push({ kind, subject, date });
    row.classList.add("flash"); setTimeout(() => row.classList.remove("flash"), 900);
    if (!selected || !pickedAt || Date.now() - pickedAt > 12000) select(company, false);
    else if (selected === company) renderTimeline();
    paint();
  }

  function reset() {
    apps = new Map(); shown = 0; selected = null;
    $("inbox").innerHTML = ""; $("rows").innerHTML = ""; $("tl").innerHTML = "";
    $("scanned").textContent = "0 scanned"; $("tl-title").textContent = "Timeline";
    paint();
  }

  async function play() {
    const my = ++run;
    reset();
    const state = $("sync-state"); state.textContent = "Syncing…"; state.classList.add("live");
    for (const ev of SCRIPT) {
      if (!(await sleep(900, my))) return;
      const card = el("li", "mail", `<b>${esc(ev[0])}</b><span>${esc(ev[1])}</span><span class="stamp s-${ev[4]}">${STAMP[ev[4]]}</span>`);
      $("inbox").prepend(card);
      $("scanned").textContent = ++shown + " scanned";
      [...$("inbox").children].slice(4).forEach((k) => { k.classList.add("out"); setTimeout(() => k.remove(), 400); });
      if (!(await sleep(500, my))) return;
      card.classList.add("scan");
      if (!(await sleep(650, my))) return;
      card.querySelector(".stamp").classList.add("hit");
      if (!(await sleep(550, my))) return;
      applyEvent(ev);
    }
    state.textContent = `Synced · ${SCRIPT.length} messages`; state.classList.remove("live");
    if (!(await sleep(6000, my))) return;
    if (my === run) play();
  }

  function finished() {
    reset();
    for (const ev of SCRIPT) {
      const card = el("li", "mail", `<b>${esc(ev[0])}</b><span>${esc(ev[1])}</span><span class="stamp s-${ev[4]} hit">${STAMP[ev[4]]}</span>`);
      $("inbox").prepend(card);
    }
    while ($("inbox").children.length > 4) $("inbox").lastElementChild.remove();
    for (const ev of SCRIPT) applyEvent(ev);
    $("scanned").textContent = SCRIPT.length + " scanned"; $("sync-state").textContent = `Synced · ${SCRIPT.length} messages`;
  }

  root.addEventListener("mouseenter", () => { paused = true; });
  root.addEventListener("mouseleave", () => { paused = false; });
  $("sync-btn").addEventListener("click", () => { paused = false; if (reduce) finished(); else play(); });

  if (reduce) { finished(); return; }
  reset();
  new IntersectionObserver(([e]) => { const was = visible; visible = e.isIntersecting; if (visible && !was && !run) play(); }, { threshold: 0.25 }).observe(root);
})();
