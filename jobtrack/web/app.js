import { reviewHTML } from "./reviews.js";
import { api, write } from "./api.js";
import {
  $,
  escapeHTML as e,
  icon,
  hydrateIcons,
  stages,
  labels,
  badge,
  avatar,
  shortDate,
  fullDate,
  relative,
  panelEmpty,
  toast,
  notice,
  gmailLink,
} from "./ui.js";
import { setupSettings } from "./settings.js";

const state = {
  page: "overview",
  category:
    localStorage.getItem("jobtrack-category") === "freelance"
      ? "freelance"
      : "employment",
  applications: [],
  overview: null,
  falsePositives: { marks: [], legacy: [], count: 0 },
  status: "all",
  search: "",
  sort: "recent",
  view: localStorage.getItem("jobtrack-view") || "list",
  asOf: "",
  activityTab: "mail",
  insights: null,
  anonymize: false,
};
let refreshVersion = 0,
  activityVersion = 0,
  detailVersion = 0,
  syncTimer,
  lastSyncState = null,
  pollFailures = 0,
  insightsVersion = 0;
const activeStages = [
  "incomplete",
  "outreach",
  "applied",
  "assessment",
  "interview",
];
const historySuffix = () =>
  state.asOf ? `?as_of=${encodeURIComponent(state.asOf)}` : "";
const categorySuffix = () =>
  `${historySuffix() || "?"}${state.asOf ? "&" : ""}category=${state.category}`;
const reference = () =>
  state.asOf ? new Date(`${state.asOf}T23:59:59Z`) : new Date();
const dateOnly = (date) =>
  date
    ? new Date(`${date}T12:00:00`).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      })
    : "—";
const eventIcon = (kind) =>
  kind === "interview"
    ? "interview"
    : kind === "offer"
      ? "offer"
      : kind === "assessment"
        ? "spark"
        : kind === "rejected"
          ? "close"
          : "mail";
const pages = ["overview", "applications", "insights", "activity"];
const stampText = (stamp) =>
  stamp?.includes("T")
    ? new Date(stamp).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : dateOnly(stamp);
const safeURL = (url) => (/^https?:\/\//i.test(url || "") ? url : "");
function go(page) {
  page = pages.includes(page)
    ? page
    : "overview";
  state.page = page;
  document.querySelectorAll(".page").forEach((el) => {
    el.hidden = el.id !== `${page}-page`;
  });
  document.querySelectorAll("[data-page]").forEach((el) => {
    const selected = el.dataset.page === page;
    el.classList.toggle("selected", selected);
    if (selected) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("breadcrumb-page").textContent = page[0].toUpperCase() + page.slice(1);
  if (location.hash !== `#${page}`) history.replaceState(null, "", `#${page}`);
  if (page === "applications") renderApplications();
  if (page === "activity") loadActivity();
  if (page === "insights") loadInsights();
}
async function refresh() {
  const version = ++refreshVersion;
  try {
    const [applications, overview, falsePositives] = await Promise.all([
      api("/api/applications" + categorySuffix()),
      api("/api/overview" + categorySuffix()),
      api("/api/false-positives"),
    ]);
    if (version !== refreshVersion) return;
    state.applications = applications.applications;
    state.falsePositives = falsePositives;
    $("false-positive-count").textContent = falsePositives.count;
    $("false-positive-count").hidden = !falsePositives.count;
    document.querySelectorAll("[data-category]").forEach((el) => {
      const selected = el.dataset.category === state.category;
      el.classList.toggle("selected", selected);
      el.setAttribute("aria-pressed", String(selected));
    });
    $("overview-title").textContent =
      state.category === "freelance"
        ? "Freelance overview"
        : "Employment overview";
    $("category-context").textContent =
      state.category === "freelance"
        ? "Independent projects & client work"
        : "Your employment search";
    $("applications-title").textContent =
      state.category === "freelance"
        ? "Freelance projects"
        : "Employment applications";
    state.overview = overview;
    $("nav-count").textContent = overview.total;
    $("history-banner").hidden = !state.asOf;
    $("history-date").textContent = dateOnly(state.asOf);
    renderOverview();
    renderApplications();
    if (state.page === "activity") loadActivity();
    if (state.page === "insights") loadInsights();
    notice("");
  } catch (error) {
    if (version === refreshVersion)
      notice(`Could not load your workspace: ${error.message}`);
  }
}
const dueCount = (data, kind, text, fallback) => {
  const n = (data.upcoming || []).filter((u) => u.kind === kind).length;
  return n ? `${n} ${text}` : fallback;
};
function renderOverview() {
  const data = state.overview;
  const metrics = [
    {
      key: "total",
      label: "Total applications",
      value: data.total,
      foot: "Tracked in this category",
      icon: "applications",
    },
    {
      key: "active",
      label: "In progress",
      value: data.active,
      foot: "Open applications",
      icon: "clock",
    },
    {
      key: "assessment",
      label: "Assessments",
      value: data.statuses.assessment || 0,
      foot: dueCount(data, "deadline", "with a deadline coming up", "Tests and take-home tasks"),
      icon: "spark",
    },
    {
      key: "interview",
      label: "Interviewing",
      value: data.interviewing,
      foot: dueCount(data, "interview", "scheduled ahead", "In interview stages"),
      icon: "interview",
    },
    {
      key: "insights",
      label: "Response rate",
      value: data.response_rate,
      foot: `${data.responses} of ${data.submitted} submitted applications`,
      icon: "activity",
      suffix: "%",
    },
  ];
  $("metrics").innerHTML = metrics
    .map(
      (m) =>
        `<button class="metric" data-metric="${m.key}" title="Show details"><div class="metric-label">${m.label}${icon(m.icon)}</div><div class="metric-value">${m.value}${m.suffix ? `<small>${m.suffix}</small>` : ""}</div><div class="metric-foot">${m.foot}</div></button>`,
    )
    .join("");
  $("welcome").hidden = data.total > 0;
  renderGoals(data.goals || []);
  $("last-sync").textContent = data.last_sync
    ? `Last synced ${relative(data.last_sync).toLowerCase()}`
    : "Ready for your first sync";
  const recent = data.weeks.slice(-8),
    total = recent.reduce((sum, w) => sum + w.applications, 0),
    max = Math.max(1, ...data.weeks.map((w) => w.applications));
  $("chart-total").textContent = total;
  $("chart-range").textContent =
    data.weeks.length > 8
      ? `since ${shortDate(data.weeks[0].week + "T12:00:00")}`
      : "weekly";
  $("weekly-chart").style.setProperty("--weeks", data.weeks.length);
  $("weekly-chart").innerHTML = data.weeks
    .map(
      (w, i) =>
        `<button class="chart-column" data-week="${i}" aria-label="Week of ${e(dateOnly(w.week))}: ${w.applications} applications, ${w.responded} got a reply"><div class="chart-bar" style="height:${w.applications ? Math.max(8, (w.applications / max) * 78) : 2}%">${w.applications ? `<div class="chart-bar-part" style="height:${(w.responded / w.applications) * 100}%"></div><span class="chart-number">${w.applications}</span>` : ""}</div><div class="chart-label">${e(dateOnly(w.week))}</div></button>`,
    )
    .join("");
  requestAnimationFrame(() => {
    $("chart-scroll").scrollLeft = $("chart-scroll").scrollWidth;
  });
  const pipelineStages = [
    "applied",
    "assessment",
    "interview",
    "offer",
    "rejected",
  ];
  if (data.statuses.ghosted) pipelineStages.push("ghosted");
  if (data.statuses.outreach) pipelineStages.unshift("outreach");
  if (data.statuses.incomplete) pipelineStages.unshift("incomplete");
  $("pipeline").innerHTML = pipelineStages
    .map(
      (kind) =>
        `<button class="pipeline-row" data-stage-list="${kind}" style="--stage-color:var(--${kind})"><span class="pipeline-name"><span class="status-dot"></span>${labels[kind]}</span><div class="pipeline-track"><div class="pipeline-fill" style="width:${data.total ? ((data.statuses[kind] || 0) / data.total) * 100 : 0}%"></div></div><span class="pipeline-count">${data.statuses[kind] || 0}</span></button>`,
    )
    .join("");
  $("attention-count").textContent = data.attention.length;
  $("attention").innerHTML = data.attention.length
    ? data.attention
        .slice(0, 7)
        .map(
          (a) =>
            `<button class="attention-item" data-app="${a.id}">${avatar(a.company)}<span class="attention-main"><strong>${e(a.company)}</strong><small>${e(a.role || "Role not stated")}</small></span><span class="attention-reason">${e(a.reason)} <span aria-hidden="true">↗</span></span></button>`,
        )
        .join("")
    : panelEmpty(
        data.total ? "No action needed" : "No tracked applications",
        data.total
          ? "Your follow-ups and applications in progress will appear here."
          : "Sync your inbox to see what deserves your attention.",
        "spark",
      );
  $("recent-activity").innerHTML = data.activity.length
    ? data.activity
        .slice(0, 7)
        .map(
          (ev) =>
            `<button class="recent-item" data-app="${ev.application_id}"><span class="event-icon">${icon(eventIcon(ev.kind))}</span><span class="recent-text"><strong>${e(ev.company)}</strong> · ${e(ev.label)}<small>${e(ev.subject)}</small></span><span class="recent-time">${e(relative(ev.date, reference()))}</span></button>`,
        )
        .join("")
    : panelEmpty(
        "No recruiting mail",
        "New recruiting messages will show up here after your first sync.",
        "mail",
      );
}
function filteredApplications() {
  const needle = state.search.toLocaleLowerCase();
  let apps = state.applications.filter(
    (a) =>
      (!needle ||
        `${a.company} ${a.role || ""}`.toLocaleLowerCase().includes(needle)) &&
      (state.status === "all" ||
        (state.status === "active"
          ? activeStages.includes(a.status)
          : a.status === state.status)),
  );
  apps.sort((a, b) =>
    state.sort === "company"
      ? a.company.localeCompare(b.company)
      : state.sort === "oldest"
        ? a.last_event_at.localeCompare(b.last_event_at)
        : state.sort === "follow_up"
          ? (a.follow_up_on || "9999").localeCompare(b.follow_up_on || "9999")
          : b.last_event_at.localeCompare(a.last_event_at),
  );
  return apps;
}
function renderApplications() {
  const counts = state.overview?.statuses || {};
  const filters = [
    ["all", "All applications", state.applications.length],
    ["active", "In progress", state.overview?.active || 0],
    ["assessment", "Assessments", counts.assessment || 0],
    ["interview", "Interviewing", counts.interview || 0],
    ["offer", "Offers", counts.offer || 0],
    ["rejected", "Rejected", counts.rejected || 0],
  ];
  if (counts.ghosted) filters.push(["ghosted", "No response", counts.ghosted]);
  $("filters").innerHTML = filters
    .map(
      ([value, label, count]) =>
        `<button data-filter="${value}" class="${state.status === value ? "selected" : ""}" aria-pressed="${state.status === value}">${label}<span class="filter-count">${count}</span></button>`,
    )
    .join("");
  $("list-view").classList.toggle("selected", state.view === "list");
  $("board-view").classList.toggle("selected", state.view === "board");
  $("list-view").setAttribute("aria-pressed", String(state.view === "list"));
  $("board-view").setAttribute("aria-pressed", String(state.view === "board"));
  const apps = filteredApplications();
  $("result-count").textContent =
    `${apps.length} of ${state.applications.length} application${state.applications.length === 1 ? "" : "s"}`;
  $("export-button").disabled = !apps.length;
  if (!apps.length) {
    const filtered = state.applications.length > 0 || state.asOf;
    $("application-content").innerHTML =
      `<div class="large-empty"><span data-icon="applications">${icon("applications")}</span><h2>${filtered ? "No matching applications" : "No applications imported"}</h2><p>${filtered ? "No applications match this view. Try a different filter, search, or history date." : "Your inbox already holds your application history. Bring it together with your first Gmail sync."}</p><button class="button button-primary" ${filtered ? "data-reset-filters" : "data-open-sync"}>${filtered ? "Reset this view" : "Import from Gmail"} ${icon("arrow")}</button></div>`;
    return;
  }
  if (state.view === "board") {
    const boardStages = [
      "applied",
      "assessment",
      "interview",
      "offer",
      "rejected",
      "ghosted",
      "outreach",
      "incomplete",
    ].filter((s) =>
      state.status === "all" || state.status === "active"
        ? state.status !== "active" || activeStages.includes(s)
        : s === state.status,
    );
    $("application-content").innerHTML = `<div class="board">${boardStages
      .map((kind) => {
        const items = apps.filter((a) => a.status === kind);
        return `<section class="board-column" data-stage="${kind}"><h2 class="board-column-heading" style="--stage-color:var(--${kind})"><span class="status-dot"></span>${labels[kind]}<span class="count-bubble">${items.length}</span></h2>${items.length ? items.map((a) => `<button class="board-card" data-app="${a.id}" draggable="${!state.asOf}"><span class="board-card-head">${avatar(a.company)}<strong>${e(a.company)}</strong></span><p>${e(a.role || "Role not stated")}</p><span class="board-card-foot"><span>${e(relative(a.last_event_at, reference()))}</span><span>${a.event_count} mail${a.event_count === 1 ? "" : "s"}</span></span>${a.follow_up_on ? `<small class="follow-up-summary">Follow up ${e(dateOnly(a.follow_up_on))}</small>` : ""}</button>`).join("") : '<p class="board-empty">No applications at this stage</p>'}</section>`;
      })
      .join("")}</div>`;
    return;
  }
  $("application-content").innerHTML =
    `<table class="application-table"><thead><tr><th scope="col">company & role</th><th scope="col">stage</th><th scope="col">last activity</th><th scope="col">messages</th><th scope="col">follow-up</th></tr></thead><tbody>${apps.map((a) => `<tr tabindex="0" data-app="${a.id}" aria-label="${e(a.company)}, ${e(a.role || "role not stated")}, ${e(labels[a.status])}"><td><div class="company-cell">${avatar(a.company)}<div><strong>${e(a.company)}</strong><small>${e(a.role || "Role not stated")}</small></div></div></td><td>${badge(a.status)}</td><td class="num">${e(relative(a.last_event_at, reference()))}<span class="date-caption">${e(fullDate(a.last_event_at))}</span></td><td class="num"><span class="message-count">${icon("mail")}${a.event_count}</span></td><td><span class="followup-date ${a.follow_up_on && a.follow_up_on <= new Date().toLocaleDateString("en-CA") ? "due" : ""}">${e(dateOnly(a.follow_up_on))}</span></td></tr>`).join("")}</tbody></table>`;
}
function detailsHTML(ev) {
  const d = ev.details;
  if (!d || !(d.when || d.deadline || d.link || d.format)) return "";
  const link = safeURL(d.link);
  const linkLabel =
    d.link_kind === "meeting"
      ? "Join meeting"
      : d.link_kind === "assessment"
        ? "Open assessment"
        : "Book a time";
  return `<div class="event-details">${d.when ? `<span class="detail-chip">${icon("calendar")}${e(stampText(d.when))}</span>` : ""}${d.deadline ? `<span class="detail-chip due">${icon("clock")}Due ${e(stampText(d.deadline))}</span>` : ""}${d.format ? `<span class="detail-chip">${e({ video: "Video call", phone: "Phone call", onsite: "On site" }[d.format] || d.format)}</span>` : ""}${link ? `<a class="text-button" href="${e(link)}" target="_blank" rel="noopener noreferrer">${linkLabel} ↗</a>` : ""}${ev.calendar_url ? `<a class="text-button" href="${e(ev.calendar_url)}" download>Add to calendar</a>` : ""}</div>`;
}
function contactsHTML(contacts) {
  if (!contacts.length) return "";
  return `<section class="detail-section"><h3>People <span class="count-bubble">${contacts.length}</span></h3><ul class="contact-list">${contacts.map((c) => `<li>${icon("user")}<span><strong>${e(c.name || c.email)}</strong><small>${c.name ? `${e(c.email)} · ` : ""}${c.messages} email${c.messages === 1 ? "" : "s"} · last ${e(relative(c.last))}</small></span><a class="text-button" href="mailto:${encodeURIComponent(c.email)}">Email ↗</a></li>`).join("")}</ul></section>`;
}
function historyHTML(a, history) {
  if (!history || (!history.applications.length && !history.cooldown))
    return "";
  const c = history.cooldown;
  return `<section class="detail-section"><h3>Also at ${e(a.company)}</h3>${c ? `<p class="cooldown-note">Rejected ${c.rejected_days_ago} days ago${c.role ? ` for ${e(c.role)}` : ""}. Many companies ask you to wait six months before reapplying, until ${e(dateOnly(c.until))}.</p>` : ""}${history.applications.map((o) => `<button class="history-row" data-app="${o.id}">${badge(o.status)}<span>${e(o.role || "Role not stated")}</span><small>${e(fullDate(o.first_seen))}</small></button>`).join("")}</section>`;
}
async function openDetail(id) {
  const version = ++detailVersion;
  try {
    const {
      application: a,
      events,
      contacts = [],
      company_history: history,
    } = await api(
      `/api/applications/${id}${historySuffix()}`,
    );
    if (version !== detailVersion) return;
    const latestURL = [...events]
      .reverse()
      .find((ev) => ev.gmail_url)?.gmail_url;
    $("detail-content").innerHTML =
      `<div class="detail-top"><span class="eyebrow">${a.category === "freelance" ? "freelance project" : "employment application"} · #${a.id}</span><button class="button icon-button" data-close="detail-dialog" aria-label="Close application details">${icon("close")}</button></div><div class="detail-head"><div class="detail-company">${avatar(a.company)}<div><h2 id="detail-title">${e(a.company)}</h2><p>${e(a.role || "Role not stated")}</p></div></div><div class="detail-head-meta">${badge(a.status)}<span>First seen ${e(fullDate(a.first_seen))}</span>${gmailLink(latestURL)}</div></div>${state.asOf ? `<div class="snapshot-detail">Email history as of ${e(dateOnly(state.asOf))}. Return to today to edit this application.</div>` : `<section class="detail-section"><details class="detail-edit"><summary>Notes & application details</summary><form id="application-edit"><div class="form-grid"><label>Company<input id="edit-company" value="${e(a.company)}" required maxlength="200"></label><label>Role<input id="edit-role" value="${e(a.role)}" maxlength="300" placeholder="Role not stated"></label></div><label>Work category<select id="edit-category"><option value="employment" ${a.category === "employment" ? "selected" : ""}>Employment</option><option value="freelance" ${a.category === "freelance" ? "selected" : ""}>Freelance</option></select></label><div class="form-grid"><label>Status correction<select id="edit-status"><option value="">Automatic · from email</option>${stages.map((k) => `<option value="${k}" ${a.status_override === k ? "selected" : ""}>${labels[k]}</option>`).join("")}</select></label><label>Follow-up date<input id="edit-followup" value="${e(a.follow_up_on)}" type="date"></label></div><label>Your notes<textarea id="edit-notes" aria-label="Your notes" maxlength="20000" placeholder="Contacts, interview notes, and next actions.">${e(a.notes)}</textarea></label><div id="edit-result" class="form-result" hidden role="status"></div><button class="button button-primary" type="submit">Save application ${icon("check")}</button></form></details>${a.notes ? `<div class="notes-preview">${e(a.notes)}</div>` : ""}${a.follow_up_on ? `<div class="follow-up-summary">${icon("calendar")}Follow up ${e(dateOnly(a.follow_up_on))}</div>` : ""}${a.status_override ? '<div class="follow-up-summary">Status corrected by you. Choose Automatic to use email history again.</div>' : ""}</section><section class="false-positive-section"><button class="button button-danger" id="mark-false-positive" type="button">Mark as not an application</button><span>Removes this application from the workspace. Its emails will stay ignored, but you can review and restore the mark later.</span></section>`}${historyHTML(a, history)}${contactsHTML(contacts)}${state.asOf || ["offer", "rejected"].includes(a.status) ? "" : `<section class="detail-section"><h3>Follow-up</h3><p class="section-hint">A draft to copy into your mail client. jobtrack can't send mail.</p><button class="button" id="draft-follow-up" type="button">${icon("mail")}Draft a follow-up</button><div id="draft-output"></div></section>`}<section class="detail-section"><h3>Email timeline <span class="count-bubble">${events.length}</span></h3><ol class="timeline">${[
        ...events,
      ]
        .reverse()
        .map(
          (ev) =>
            `<li class="timeline-event" style="--stage-color:var(--${stages.includes(ev.kind) ? ev.kind : "applied"})"><div class="timeline-head">${badge(ev.kind)}<time datetime="${e(ev.date)}">${e(new Date(ev.date).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" }))}</time></div><h4>${e(ev.subject || "(No subject)")}</h4><div class="timeline-sender">${e(ev.sender)}</div>${ev.snippet ? `<p class="timeline-snippet">${e(ev.snippet)}</p>` : ""}${detailsHTML(ev)}${gmailLink(ev.gmail_url)}<details class="classification"><summary>Classification details</summary><p>${ev.source === "manual" ? "Added by you" : ev.source === "llm" ? "Model analysis" : "Pattern rules"} · ${Math.round(ev.confidence * 100)}% confidence</p>${reviewHTML(ev.review)}</details></li>`,
        )
        .join("")}</ol></section>`;
    if (!$("detail-dialog").open) $("detail-dialog").showModal();
    else $("detail-dialog").querySelector("button").focus();
    $("application-edit")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.submitter;
      button.disabled = true;
      try {
        await write(
          `/api/applications/${a.id}`,
          {
            company: $("edit-company").value,
            role: $("edit-role").value,
            notes: $("edit-notes").value,
            category: $("edit-category").value,
            follow_up_on: $("edit-followup").value || null,
            status_override: $("edit-status").value || null,
          },
          "PATCH",
        );
        if ($("edit-category").value !== state.category) {
          state.category = $("edit-category").value;
          localStorage.setItem("jobtrack-category", state.category);
          state.status = "all";
          state.search = "";
          $("search").value = "";
        }
        toast("Application updated");
        await refresh();
        await openDetail(a.id);
      } catch (error) {
        $("edit-result").hidden = false;
        $("edit-result").textContent = error.message;
        $("edit-result").classList.add("error");
        button.disabled = false;
      }
    });
    $("draft-follow-up")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.lastChild.textContent = "Drafting…";
      try {
        const draft = await api(`/api/applications/${a.id}/follow-up-draft`, {
          method: "POST",
        });
        $("draft-output").innerHTML =
          `<textarea class="draft-text" aria-label="Follow-up draft" rows="10">${e(draft.text)}</textarea><div class="draft-actions"><button class="button" type="button" id="draft-copy">${icon("copy")}Copy</button><span class="section-hint">${draft.source === "model" ? "Written by your model from the timeline. Check it before sending." : "From a template." + (draft.error ? " The model was unavailable." : "")}</span></div>`;
        $("draft-copy").addEventListener("click", async () => {
          await navigator.clipboard.writeText(
            $("draft-output").querySelector("textarea").value,
          );
          toast("Draft copied");
        });
        button.lastChild.textContent = "Draft again";
      } catch (error) {
        toast(error.message);
        button.lastChild.textContent = "Draft a follow-up";
      }
      button.disabled = false;
    });
    $("mark-false-positive")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      if (
        !window.confirm(
          "Remove this application and ignore all its emails in future syncs? You can restore it later from “Marked as not applications”.",
        )
      )
        return;
      button.disabled = true;
      try {
        await api(`/api/applications/${a.id}/false-positive`, {
          method: "DELETE",
        });
        $("detail-dialog").close();
        await refresh();
        toast("Application removed. Its emails stay ignored until you restore it.");
      } catch (error) {
        toast(error.message);
        button.disabled = false;
      }
    });
  } catch (error) {
    notice(`Could not open that application: ${error.message}`);
  }
}
function openFalsePositives() {
  $("false-positive-content").innerHTML =
    '<p class="mark-loading">Loading marked applications…</p>';
  if (!$("false-positive-dialog").open) $("false-positive-dialog").showModal();
  loadFalsePositives();
}
async function loadFalsePositives() {
  try {
    const data = await api("/api/false-positives");
    state.falsePositives = data;
    $("false-positive-count").textContent = data.count;
    $("false-positive-count").hidden = !data.count;
    renderFalsePositives(data);
  } catch (error) {
    $("false-positive-content").innerHTML =
      `<p class="mark-error">Could not load marked applications: ${e(error.message)}</p>`;
  }
}
function renderFalsePositives(data) {
  if (!data.count) {
    $("false-positive-content").innerHTML =
      `<div class="large-empty"><span data-icon="check">${icon("check")}</span><h2>Nothing marked</h2><p>Applications you mark as “not an application” will appear here so you can review and restore them.</p></div>`;
    return;
  }
  const marks = data.marks
    .map(
      (mark) => `<article class="mark-card"><div class="mark-card-head"><strong>${e(mark.company)}</strong><span>Marked ${e(fullDate(mark.created_at))}</span></div><p>${e(mark.role || "Role not stated")} · ${mark.message_count} email${mark.message_count === 1 ? "" : "s"} saved${mark.restorable ? "" : " · no snapshot"}</p>${mark.restorable ? `<button class="button button-primary" type="button" data-restore-mark="${mark.id}">Restore application ${icon("arrow")}</button>` : '<p class="mark-warning">This mark has no saved application snapshot. Its emails are listed below for recovery from Gmail.</p>'}</article>`,
    )
    .join("");
  const legacy = data.legacy
    .map(
      (mark) => `<article class="mark-card legacy-mark"><div class="mark-card-head"><strong>Marked email</strong><code>${e(mark.message_id)}</code></div><p>No application details were saved when this mark was made.</p><button class="button" type="button" data-restore-legacy="${e(mark.message_id)}">Recover from Gmail ${icon("sync")}</button></article>`,
    )
    .join("");
  $("false-positive-content").innerHTML =
    `${legacy ? `<section class="mark-group"><h3>Older marks without snapshots <span class="count-bubble">${data.legacy.length}</span></h3><p class="mark-warning">These marks were made before JobTrack saved application snapshots. Each Gmail message can be fetched and classified again, but its company and role come only from that message, and its notes, status corrections, follow-up date, and exact place in the original timeline were never stored. Only the message evidence can be recovered.</p>${legacy}</section>` : ""}${marks ? `<section class="mark-group"><h3>Marked applications <span class="count-bubble">${data.marks.length}</span></h3>${marks}</section>` : ""}`;
}
async function restoreMark(markId, button) {
  button.disabled = true;
  try {
    const { application } = await api(
      `/api/false-positives/${markId}/restore`,
      { method: "POST" },
    );
    toast(`${application.company} restored with its saved timeline.`);
    await refresh();
    await loadFalsePositives();
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  }
}
async function restoreLegacyMark(messageId, button) {
  button.disabled = true;
  try {
    const { application } = await api(
      `/api/false-positives/legacy/${encodeURIComponent(messageId)}/restore`,
      { method: "POST" },
    );
    toast(`${application.company} recovered from its Gmail message.`);
    await refresh();
    await loadFalsePositives();
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  }
}
async function loadActivity() {
  const version = ++activityVersion;
  $("mail-tab").classList.toggle("selected", state.activityTab === "mail");
  $("runs-tab").classList.toggle("selected", state.activityTab === "runs");
  try {
    const data = await api(
      state.activityTab === "runs"
        ? "/api/sync/history"
        : `/api/activity${categorySuffix()}`,
    );
    if (version !== activityVersion) return;
    if (state.activityTab === "runs") {
      $("activity-content").innerHTML = data.runs.length
        ? data.runs
            .map(
              (run) =>
                `<article class="run-card"><div class="run-head"><strong>${run.dry_run ? "Inbox preview" : "Gmail sync"} <span aria-hidden="true">·</span> ${run.status === "done" ? "Complete" : run.status === "running" ? "Running" : "Needs attention"}</strong><span>${e(fullDate(run.started_at))} · ${e(new Date(run.started_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }))}</span></div>${run.error ? `<p class="run-error">${e(run.error)}</p>` : ""}${run.summary ? `<div class="run-summary"><span><strong>${run.summary.scanned}</strong>scanned</span><span><strong>${run.summary.created}</strong>${run.dry_run ? "candidate emails" : "new applications"}</span><span><strong>${run.summary.matched}</strong>updates</span><span><strong>${run.summary.duplicate}</strong>already processed</span>${run.summary.sent ? `<span><strong>${run.summary.sent}</strong>sent by you</span>` : ""}${run.summary.reviewed ? `<span><strong>${run.summary.reviewed}</strong>team reviews</span><span><strong>${run.summary.unresolved || 0}</strong>need review</span>` : ""}</div>` : `<p>${run.scanned} emails scanned so far</p>`}<details class="run-query"><summary>Gmail query</summary><code>${e(run.query)}</code></details></article>`,
            )
            .join("")
        : `<div class="large-empty"><span data-icon="sync">${icon("sync")}</span><h2>No sync history</h2><p>Your previews and syncs will appear here, including any failures.</p><button class="button button-primary" data-open-sync>Set up a sync ${icon("arrow")}</button></div>`;
      return;
    }
    const grouped = new Map();
    data.events.forEach((ev) => {
      const day = fullDate(ev.date);
      if (!grouped.has(day)) grouped.set(day, []);
      grouped.get(day).push(ev);
    });
    $("activity-content").innerHTML = grouped.size
      ? [...grouped]
          .map(
            ([day, events]) =>
              `<section class="activity-group"><h2 class="activity-date">${e(day)}</h2>${events.map((ev) => `<button class="activity-row" data-app="${ev.application_id}">${avatar(ev.company)}<div><h3>${e(ev.company)} <span aria-hidden="true">·</span> ${e(ev.subject || "(No subject)")}</h3><p>${badge(ev.kind)}${e(ev.role || "Role not stated")}</p><small>${e(ev.snippet?.slice(0, 180))}</small></div><time datetime="${e(ev.date)}">${e(new Date(ev.date).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }))}</time></button>`).join("")}</section>`,
          )
          .join("") +
        '<p class="page-footnote">Showing up to 100 recent recruiting emails.</p>'
      : `<div class="large-empty"><span data-icon="mail">${icon("mail")}</span><h2>No events recorded</h2><p>Sync Gmail to bring your recruiting conversations into one place.</p><button class="button button-primary" data-open-sync>Import from Gmail ${icon("arrow")}</button></div>`;
  } catch (error) {
    if (version === activityVersion)
      notice(`Could not load activity: ${error.message}`);
  }
}
function openSync() {
  closeSyncMenu();
  if (!$("sync-dialog").open) $("sync-dialog").showModal();
}
function closeSyncMenu() {
  $("sync-menu").hidden = true;
  $("sync-menu-toggle").setAttribute("aria-expanded", "false");
}
function syncResult(message, error = false, running = false) {
  $("sync-result").hidden = false;
  $("sync-result").className =
    `sync-result${error ? " error" : ""}${running ? " running" : ""}`;
  $("sync-result").textContent = message;
  if (running) {
    const line = document.createElement("div");
    line.className = "progress-line";
    $("sync-result").append(line);
  }
}
function showSync(run, initial = false) {
  const previous = lastSyncState;
  lastSyncState = run;
  const running = run.status === "running";
  ["sync-button", "sync-all", "sync-submit", "preview-button"].forEach((id) => {
    $(id).disabled = running;
  });
  $("sync-button-label").textContent = running ? "Syncing…" : "Sync Gmail";
  $("sync-dock").hidden = !running;
  if (running) {
    $("dock-title").textContent = run.dry_run
      ? "Previewing your inbox"
      : "Syncing your inbox";
    $("dock-description").textContent =
      `${run.scanned || 0} emails scanned · you can keep browsing`;
    syncResult(
      `${run.dry_run ? "Previewing" : "Syncing"} · ${run.scanned || 0} emails scanned.${run.note ? " " + run.note : ""}`,
      false,
      true,
    );
    clearTimeout(syncTimer);
    syncTimer = setTimeout(pollSync, 1600);
  } else if (run.status === "done") {
    const s = run.summary || {};
    syncResult(
      run.dry_run
        ? `Preview complete. ${s.created || 0} candidate emails from ${s.scanned || 0} scanned. Application history was not changed.`
        : `All up to date. ${s.created || 0} new applications, ${s.matched || 0} updates. ${s.scanned || 0} emails checked; ${s.duplicate || 0} already synced emails skipped.`,
    );
    if (s.unresolved)
      $("sync-result").append(
        document.createTextNode(
          ` ${s.unresolved} email${s.unresolved === 1 ? "" : "s"} need manual review. See recent consensus decisions in settings.`,
        ),
      );
    if (s.llm_disabled_reason) {
      $("sync-result").append(
        document.createTextNode(
          ` Model unavailable: ${s.llm_disabled_reason}. Rules were used instead.`,
        ),
      );
    }
    if (run.dry_run && run.candidates?.length)
      $("sync-preview").innerHTML =
        `<h3 class="preview-heading">${run.candidates.length} candidates${s.created > 100 ? " · showing the first 100" : ""}</h3><div class="preview-list">${run.candidates.map((c) => `<div class="preview-row"><strong>${e(c.company)}</strong>${badge(c.kind)}<p>${e(c.role || "Role not stated")} · ${e(c.subject)}</p><small>${c.source === "llm" ? "Model analysis" : "Rules"} · ${Math.round(c.confidence * 100)}% confidence${c.review?.status === "unresolved" ? " · Needs review" : c.review ? " · Consensus" : ""}</small>${c.review ? `<details><summary>Reviewer verdicts</summary>${reviewHTML(c.review)}</details>` : ""}</div>`).join("")}</div>`;
    if (
      !initial &&
      (previous?.status === "running" ||
        (previous?.id && previous.id !== run.id))
    ) {
      refresh();
      toast(
        run.dry_run ? "Inbox preview is ready" : "Your workspace is up to date",
      );
    }
  } else if (run.status === "error") {
    syncResult(run.error || "The sync could not finish. Try again.", true);
    if (!initial && previous?.status === "running") {
      refresh().then(() => notice(run.error));
    }
  }
  if (!running) {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(pollSync, 15000);
  }
}
async function pollSync() {
  try {
    const run = await api("/api/sync");
    pollFailures = 0;
    showSync(run);
  } catch (error) {
    pollFailures++;
    if (pollFailures === 1) toast("Checking sync status again shortly…");
    syncTimer = setTimeout(pollSync, Math.min(10000, 2000 * pollFailures));
  }
}
async function startSync(preview = false, quick = false) {
  closeSyncMenu();
  if (lastSyncState?.status === "running") {
    openSync();
    return;
  }
  $("sync-preview").replaceChildren();
  syncResult(
    preview ? "Starting your preview…" : "Starting your sync…",
    false,
    true,
  );
  ["sync-button", "sync-all", "sync-submit", "preview-button"].forEach((id) => {
    $(id).disabled = true;
  });
  const payload = quick
    ? quick === "all"
      ? {
          since: $("sync-since").value || "365",
          before: $("sync-before").value || null,
          limit: Number($("sync-limit").value),
          recheck: true,
        }
      : { limit: 0, recheck: false }
    : {
        since: $("sync-since").value || "365",
        before: $("sync-before").value || null,
        limit: Number($("sync-limit").value),
        recheck: $("sync-mode").value === "full",
        track_sent: $("sync-track-sent").checked,
        query: $("sync-query").value.trim() || null,
        dry_run: preview,
      };
  try {
    showSync(await write("/api/sync", payload));
  } catch (error) {
    syncResult(error.message, true);
    ["sync-button", "sync-all", "sync-submit", "preview-button"].forEach(
      (id) => {
        $(id).disabled = false;
      },
    );
    if (quick) {
      notice(error.message);
      openSync();
    }
  }
}
function exportCSV() {
  const query = new URLSearchParams({
    category: state.category,
    ids: filteredApplications()
      .map((a) => a.id)
      .join(","),
  });
  if (state.asOf) query.set("as_of", state.asOf);
  const link = document.createElement("a");
  link.href = "/api/export?" + query;
  link.download = `jobtrack-${state.category}${state.asOf ? "-" + state.asOf : ""}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  toast("Your export download was requested");
}

// -- insights -------------------------------------------------------------
async function loadInsights() {
  const version = ++insightsVersion;
  try {
    const data = await api("/api/insights" + categorySuffix());
    if (version !== insightsVersion) return;
    state.insights = data;
    renderInsights();
  } catch (error) {
    if (version === insightsVersion)
      $("insights-content").innerHTML =
        `<p class="mark-error">Could not load insights: ${e(error.message)}</p>`;
  }
}
function companyName() {
  const names = new Map();
  return (company) => {
    if (!state.anonymize) return company;
    if (!names.has(company))
      names.set(
        company,
        "Company " +
          (names.size < 26
            ? String.fromCharCode(65 + names.size)
            : names.size + 1),
      );
    return names.get(company);
  };
}

// -- detail popups ----------------------------------------------------------
// Every number that stands for a set of applications opens this list.
let lists = [],
  listName = (company) => company;
function listButton(spec) {
  lists.push(spec);
  return `data-list="${lists.length - 1}"`;
}
function openList({ eyebrow = "details", title, description = "", ids = [], notes = {}, stats = "" }) {
  const byId = new Map(state.applications.map((a) => [a.id, a]));
  const apps = ids.map((id) => byId.get(id)).filter(Boolean);
  $("list-eyebrow").textContent = eyebrow;
  $("list-title").textContent = title;
  $("list-description").textContent = description;
  $("list-content").innerHTML =
    stats +
    (apps.length
      ? `<div class="list-rows">${apps
          .map(
            (a) =>
              `<button class="list-row" data-app="${a.id}">${state.anonymize && state.page === "insights" ? "" : avatar(a.company)}<span class="list-main"><strong>${e(listName(a.company))}</strong><small>${e(a.role || "Role not stated")}</small></span>${notes[a.id] ? `<span class="list-note">${e(notes[a.id])}</span>` : ""}${badge(a.status)}<span class="list-date">${e(fullDate(a.first_seen))}</span></button>`,
          )
          .join("")}</div>`
      : panelEmpty("Nothing here", "No applications match this selection.", "applications"));
  if (!$("list-dialog").open) $("list-dialog").showModal();
}
function openWeek(index) {
  const weeks = state.overview.weeks,
    w = weeks[index];
  const end = new Date(`${w.week}T12:00:00`);
  end.setDate(end.getDate() + 6);
  const previous = weeks[index - 1];
  const change = previous
    ? w.applications - previous.applications
    : null;
  listName = (company) => company;
  openList({
    eyebrow: "application volume",
    title: `Week of ${dateOnly(w.week)} – ${end.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`,
    description: "Applications first seen this week. Select one to open its timeline.",
    ids: w.ids,
    stats: `<div class="list-stats"><span><strong>${w.applications}</strong>applications</span><span><strong>${w.responded}</strong>got a reply</span><span><strong>${w.applications ? Math.round((100 * w.responded) / w.applications) : 0}%</strong>reply rate</span>${change === null ? "" : `<span><strong>${change > 0 ? "+" : ""}${change}</strong>vs the week before</span>`}</div>`,
  });
}
function openStage(kind) {
  listName = (company) => company;
  const apps = state.applications
    .filter((a) => a.status === kind)
    .sort((a, b) => b.last_event_at.localeCompare(a.last_event_at));
  openList({
    eyebrow: "pipeline",
    title: labels[kind],
    description: `${apps.length} application${apps.length === 1 ? "" : "s"} at this stage, most recent activity first.`,
    ids: apps.map((a) => a.id),
    notes: Object.fromEntries(apps.map((a) => [a.id, `active ${relative(a.last_event_at, reference())}`])),
  });
}
function openMetric(key) {
  const data = state.overview;
  if (key === "insights") return go("insights");
  state.status = key === "total" ? "all" : key;
  state.search = "";
  $("search").value = "";
  go("applications");
}

function barRows(rows, color = "var(--accent)") {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return `<div class="pipeline insight-bars">${rows
    .map(
      (r) =>
        `<${r.list ? `button ${listButton(r.list)}` : "div"} class="pipeline-row" style="--stage-color:${r.color || color}"><span class="pipeline-name" title="${e(r.label)}">${e(r.label)}</span><div class="pipeline-track"><div class="pipeline-fill" style="width:${((r.scale ?? r.value) / (r.scale !== undefined ? 100 : max)) * 100}%"></div></div><span class="pipeline-count">${e(r.text ?? r.value)}</span></${r.list ? "button" : "div"}>`,
    )
    .join("")}</div>`;
}
const panel = (title, hint, body, id = "") =>
  `<section class="panel"${id ? ` id="${id}"` : ""}><div class="panel-heading"><div><h2>${title}</h2><p>${hint}</p></div></div>${body}</section>`;
function renderInsights() {
  const d = state.insights;
  if (!d) return;
  const s = d.summary;
  lists = [];
  if (!s.applications) {
    $("insights-content").innerHTML =
      `<div class="large-empty"><span data-icon="insights">${icon("insights")}</span><h2>Nothing to measure yet</h2><p>Insights appear once applications are imported or added.</p><button class="button button-primary" data-open-sync>Import from Gmail ${icon("arrow")}</button></div>`;
    return;
  }
  const name = companyName();
  listName = name;
  const eyebrow = "insights";
  const metric = (label, value, foot, list) =>
    `<button class="metric" ${list ? listButton(list) : 'data-scroll="insight-companies"'}><div class="metric-label">${label}</div><div class="metric-value">${value}</div><div class="metric-foot">${foot}</div></button>`;
  const waits = Object.fromEntries(s.measured.map((m) => [m.id, `${m.days} day${m.days === 1 ? "" : "s"} to reply`]));
  const summary = `<div class="metrics">${metric("Companies", s.companies, s.repeat_companies ? `${s.repeat_companies} with more than one application` : `since ${e(dateOnly(s.started))}`)}${metric("Typical wait", s.median_days_to_response ?? "—", s.median_days_to_response === null ? "Not enough replies to measure" : `days to a reply · ${s.measured_responses} measured`, { eyebrow, title: "Time to a reply", description: "Applications with a measurable wait, slowest first.", ids: s.measured.map((m) => m.id), notes: waits })}${s.assessments ? metric("Assessment pass rate", `${s.assessment_pass_rate}<small>%</small>`, `${s.assessments_passed} of ${s.assessments} led to an interview`, { eyebrow, title: "Assessments", description: `${s.assessments_passed} of ${s.assessments} applications with an assessment went on to an interview or offer. Passed ones are marked.`, ids: s.assessment_ids, notes: Object.fromEntries(s.assessment_passed_ids.map((id) => [id, "passed"])) }) : ""}${metric("Interview rate", `${s.interview_rate}<small>%</small>`, `${s.interviews} of ${s.submitted} reached an interview`, { eyebrow, title: "Reached an interview", description: "Submitted applications with an interview or offer.", ids: d.funnel[3].ids })}${metric("No response", s.ghosted, s.ghost_after_days ? `${s.ghost_rate}% · silent for ${s.ghost_after_days}+ days` : "Turned off in settings", { eyebrow, title: "No response", description: `Submitted applications with no reply for more than ${s.ghost_after_days} days. A reply moves them back automatically.`, ids: s.ghosted_ids })}</div>`;
  const highlights = d.highlights.length
    ? `<section class="panel highlights"><ul>${d.highlights.map((h) => `<li>${icon("spark")}${e(state.anonymize ? h.replace(/^You applied to .+ most often.*$/, "One company received several of your applications.") : h)}</li>`).join("")}</ul></section>`
    : "";
  const funnel = barRows(
    d.funnel.map((f) => ({
      label: f.label,
      value: f.count,
      scale: f.rate,
      text: `${f.count} · ${f.rate}%`,
      color: `var(--${{ submitted: "applied", responded: "outreach" }[f.key] || f.key})`,
      list: { eyebrow: "funnel", title: f.label, description: `${f.count} of ${s.submitted} submitted applications (${f.rate}%).`, ids: f.ids },
    })),
  );
  const shown = state.allCompanies ? d.companies : d.companies.slice(0, 10);
  const companies = `<table class="application-table insight-table"><thead><tr><th scope="col">company</th><th scope="col" class="num">applications</th><th scope="col" class="num">replies</th><th scope="col">furthest stage</th><th scope="col" class="num">reply time</th><th scope="col">now</th></tr></thead><tbody>${shown
    .map(
      (c) =>
        `<tr tabindex="0" ${listButton({ eyebrow: "company", title: name(c.company), description: `${c.applications} application${c.applications === 1 ? "" : "s"} · ${c.responded} repl${c.responded === 1 ? "y" : "ies"}${c.best_stage_label ? ` · furthest stage: ${c.best_stage_label}` : ""}${c.days_to_response === null ? "" : ` · typical reply in ${c.days_to_response} days`}`, ids: c.application_ids })}><td><div class="company-cell">${state.anonymize ? "" : avatar(c.company)}<strong>${e(name(c.company))}</strong></div></td><td class="num">${c.applications}</td><td class="num">${c.responded}</td><td>${e(c.best_stage_label || "—")}</td><td class="num">${c.days_to_response === null ? "—" : `${c.days_to_response}d`}</td><td>${badge(c.status)}</td></tr>`,
    )
    .join(
      "",
    )}</tbody></table>${d.companies.length > 10 ? `<button class="text-button" id="all-companies">${state.allCompanies ? "Show top 10" : `Show all ${d.companies.length} companies`}</button>` : ""}`;
  const speed = (list) =>
    list
      .map(
        (c) =>
          `<li><button ${listButton({ eyebrow: "company", title: name(c.company), description: `Typical reply in ${c.days_to_response} days.`, ids: c.application_ids })}><span>${e(name(c.company))}</span><strong>${c.days_to_response}d</strong></button></li>`,
      )
      .join("");
  const timing = `${barRows(
    d.response_times.map((r) => ({
      label: r.label,
      value: r.count,
      list: { eyebrow: "time to reply", title: `Replied in ${r.label.toLowerCase()}`, ids: r.ids, notes: waits },
    })),
    "var(--interview)",
  )}${d.fastest.length ? `<div class="speed-lists"><div><h3>Fastest</h3><ol>${speed(d.fastest)}</ol></div>${d.slowest.length ? `<div><h3>Slowest</h3><ol>${speed(d.slowest)}</ol></div>` : ""}</div>` : ""}`;
  const channels = d.channels.length
    ? barRows(
        d.channels.map((r) => ({
          label: r.name,
          value: r.rate,
          scale: r.rate,
          text: `${r.rate}% of ${r.submitted}`,
          list: { eyebrow: "channel", title: r.name, description: `${r.responded} of ${r.submitted} got a reply (${r.rate}%).`, ids: r.ids },
        })),
      )
    : panelEmpty("Nothing submitted yet", "", "applications");
  const roles = barRows(
    d.roles.map((r, i) => ({
      label: r.name,
      value: r.count,
      text: `${r.count} · ${r.submitted ? `${r.rate}% reply` : "—"}`,
      color: `var(--role-${i % 8})`,
      list: { eyebrow: "role type", title: r.name, description: `${r.count} application${r.count === 1 ? "" : "s"} · ${r.responded} of ${r.submitted} submitted got a reply.`, ids: r.ids },
    })),
  );
  const rejections = d.rejections.total
    ? `${barRows(
        d.rejections.reasons.map((r) => ({
          label: r.label,
          value: r.count,
          list: { eyebrow: "rejections", title: r.label, ids: r.ids },
        })),
        "var(--rejected)",
      )}${d.rejections.quick ? `<button class="text-button section-link" ${listButton({ eyebrow: "rejections", title: "Rejected within 3 days", description: "Fast rejections without an earlier reply are often automated screening.", ids: d.rejections.quick_ids })}>${d.rejections.quick} of ${d.rejections.total} arrived within 3 days of applying, often a sign of automated screening ↗</button>` : `<p class="section-hint">None arrived within 3 days of applying, so automated screening isn't filtering you out.</p>`}`
    : panelEmpty("No rejections", "Nothing to analyze here, which is good news.", "check");
  $("insights-content").innerHTML =
    summary +
    highlights +
    `<div class="overview-grid">${panel("Funnel", "Share of submitted applications reaching each stage", funnel)}${panel("Channels", "Reply rate by where the application started", channels)}</div>` +
    `<div class="overview-grid role-grid">${panel("Companies", "Grouped by employer, most applications first. Select one for its applications.", companies, "insight-companies")}</div>` +
    `<div class="overview-grid">${panel("Time to reply", "Days from the first email to the first reply", timing)}${panel("Rejections", "Why, as far as the email says", rejections)}</div>` +
    `<div class="overview-grid role-grid">${panel("Role types", "Applications per role category, and how often each gets a reply", roles)}</div>`;
  $("all-companies")?.addEventListener("click", () => {
    state.allCompanies = !state.allCompanies;
    renderInsights();
  });
}

// -- digest ---------------------------------------------------------------
const digestNouns = {
  applied: ["confirmation", "confirmations"],
  assessment: ["assessment", "assessments"],
  interview: ["interview update", "interview updates"],
  offer: ["offer", "offers"],
  rejected: ["rejection", "rejections"],
  outreach: ["recruiter message", "recruiter messages"],
  incomplete: ["unfinished application", "unfinished applications"],
};
async function loadDigest() {
  const key = "jobtrack-last-visit";
  const last = localStorage.getItem(key);
  localStorage.setItem(key, new Date().toISOString());
  if (!last || state.asOf) return;
  try {
    const d = await api(
      `/api/digest?since=${encodeURIComponent(last)}&category=${state.category}`,
    );
    if (!d.total) return;
    const counts = Object.entries(d.counts)
      .map(([k, n]) => `${n} ${(digestNouns[k] || [k, k])[n === 1 ? 0 : 1]}`)
      .join(", ");
    $("digest").innerHTML =
      `<div><strong>Since your last visit ${e(relative(last).toLowerCase())}</strong><span>${e(counts)}</span></div><div class="digest-items">${d.events
        .slice(0, 5)
        .map(
          (ev) =>
            `<button class="text-button" data-app="${ev.application_id}">${e(ev.company)} · ${e(ev.label)}</button>`,
        )
        .join(
          "",
        )}</div><button class="button icon-button" id="digest-close" aria-label="Dismiss">${icon("close")}</button>`;
    $("digest").hidden = false;
    $("digest-close").addEventListener("click", () => {
      $("digest").hidden = true;
    });
  } catch {
    // The banner is a convenience; the overview below already has the facts.
  }
}

// -- add an application ---------------------------------------------------
let lookupTimer;
function openAdd() {
  $("add-form").reset();
  $("add-date").value = new Date().toLocaleDateString("en-CA");
  $("add-category").value = state.category;
  $("add-lookup").hidden = true;
  $("add-result").hidden = true;
  $("add-dialog").showModal();
  $("add-company").focus();
}
async function lookupCompany() {
  const q = $("add-company").value.trim();
  if (q.length < 2) {
    $("add-lookup").hidden = true;
    return;
  }
  try {
    const { results } = await api(`/api/lookup?q=${encodeURIComponent(q)}`);
    if (q !== $("add-company").value.trim()) return;
    $("add-lookup").hidden = !results.length;
    $("add-lookup").innerHTML = results
      .slice(0, 3)
      .map(
        (r) =>
          `<p><strong>Already tracked: ${e(r.company)}</strong> · ${r.applications.map((a) => `${e(a.role || "Role not stated")} (${e(a.status_label)}, ${e(fullDate(a.first_seen))})`).join("; ")}</p>${r.cooldown ? `<p class="cooldown-note">Rejected ${r.cooldown.rejected_days_ago} days ago. Many companies ask you to wait until ${e(dateOnly(r.cooldown.until))}.</p>` : ""}`,
      )
      .join("");
  } catch {
    $("add-lookup").hidden = true;
  }
}
async function submitAdd(event) {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const category = $("add-category").value;
    const { application } = await write("/api/applications", {
      company: $("add-company").value,
      role: $("add-role").value || null,
      category,
      applied_on: $("add-date").value || null,
      notes: $("add-notes").value || null,
    });
    $("add-dialog").close();
    if (category !== state.category) {
      state.category = category;
      localStorage.setItem("jobtrack-category", category);
    }
    toast(`${application.company} added`);
    await refresh();
    openDetail(application.id);
  } catch (error) {
    $("add-result").hidden = false;
    $("add-result").textContent = error.message;
    $("add-result").classList.add("error");
  }
  button.disabled = false;
}

// -- goals ------------------------------------------------------------------
const goalMetrics = {
  applications: "Applications",
  replies: "Replies",
  assessments: "Assessments",
  interviews: "Interviews",
  offers: "Offers",
};
function renderGoals(goals) {
  $("goals-panel").hidden = !!state.asOf;
  $("goals").innerHTML = goals.length
    ? goals
        .map((g, i) => {
          const done = g.current >= g.target;
          return `<button class="goal-row${done ? " met" : ""}" data-goal="${i}"><span class="goal-name">${e(g.label)}</span><div class="pipeline-track"><div class="pipeline-fill" style="width:${Math.min(100, (g.current / g.target) * 100)}%"></div></div><span class="goal-count"><strong>${g.current}</strong>/${g.target}</span><span class="goal-foot">${done ? "Met" : `${g.target - g.current} to go`}${g.streak ? ` · ${g.streak}-${g.period} streak` : ""}</span></button>`;
        })
        .join("")
    : `<div class="goals-empty"><span>No goals yet. Set a target, such as 10 applications a week or 2 interviews a month.</span><button class="button" data-goals-open>${icon("plus")}Set a goal</button></div>`;
}
function openGoal(index) {
  const g = state.overview.goals[index];
  listName = (company) => company;
  openList({
    eyebrow: "goal",
    title: `${g.label}: ${g.current} of ${g.target}`,
    description: `Counted since ${dateOnly(g.period_start)}${g.streak ? ` · ${g.streak}-${g.period} streak` : ""}.`,
    ids: g.ids,
  });
}
let goalDrafts = [];
function goalEditorRow(goal, i) {
  return `<div class="goal-edit-row" data-index="${i}"><label>Count<select data-field="metric">${Object.entries(goalMetrics).map(([k, v]) => `<option value="${k}" ${goal.metric === k ? "selected" : ""}>${v}</option>`).join("")}</select></label><label>Target<input data-field="target" type="number" min="1" max="1000" required value="${goal.target}"></label><label>Every<select data-field="period"><option value="week" ${goal.period === "week" ? "selected" : ""}>Week</option><option value="month" ${goal.period === "month" ? "selected" : ""}>Month</option></select></label><button class="button icon-button" type="button" data-goal-remove="${i}" aria-label="Remove goal">${icon("close")}</button></div>`;
}
function renderGoalEditor() {
  $("goals-editor").innerHTML = goalDrafts.length
    ? goalDrafts.map(goalEditorRow).join("")
    : '<p class="section-hint">No goals. Add one below.</p>';
  $("goal-add").hidden = goalDrafts.length >= 6;
}
function readGoalEditor() {
  goalDrafts = [...$("goals-editor").querySelectorAll(".goal-edit-row")].map((row) => ({
    metric: row.querySelector('[data-field="metric"]').value,
    period: row.querySelector('[data-field="period"]').value,
    target: Number(row.querySelector('[data-field="target"]').value),
  }));
}
function openGoals() {
  goalDrafts = (state.overview?.goals || []).map(({ metric, period, target }) => ({ metric, period, target }));
  if (!goalDrafts.length) goalDrafts.push({ metric: "applications", period: "week", target: 10 });
  $("goals-result").hidden = true;
  renderGoalEditor();
  $("goals-dialog").showModal();
}
async function saveGoals(event) {
  event.preventDefault();
  readGoalEditor();
  try {
    await write("/api/settings", { goals: goalDrafts }, "PUT");
    $("goals-dialog").close();
    toast("Goals saved");
    refresh();
  } catch (error) {
    $("goals-result").hidden = false;
    $("goals-result").textContent = error.message;
    $("goals-result").classList.add("error");
  }
}

// -- board drag and drop ----------------------------------------------------
async function moveApplication(id, stage) {
  const app = state.applications.find((a) => String(a.id) === String(id));
  if (!app || app.status === stage || stage === "ghosted") return;
  try {
    await write(`/api/applications/${id}`, { status_override: stage }, "PATCH");
    toast(
      `${app.company} moved to ${labels[stage]}. Choose Automatic in its details to undo.`,
    );
    await refresh();
  } catch (error) {
    toast(error.message);
  }
}

hydrateIcons();
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const label =
    theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  $("theme-toggle").innerHTML = icon(theme === "dark" ? "sun" : "moon");
  $("theme-toggle").setAttribute("aria-label", label);
  $("theme-toggle").title = label;
  document.querySelector('meta[name="theme-color"]').content =
    theme === "dark" ? "#131210" : "#f5f3ee";
}
applyTheme(document.documentElement.dataset.theme || "light");
$("theme-toggle").addEventListener("click", () => {
  const next =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("jobtrack-theme", next);
  applyTheme(next);
});
matchMedia("(prefers-color-scheme: dark)").addEventListener(
  "change",
  (event) => {
    if (!localStorage.getItem("jobtrack-theme"))
      applyTheme(event.matches ? "dark" : "light");
  },
);
$("today").textContent = new Date().toLocaleDateString(undefined, {
  weekday: "short",
  day: "numeric",
  month: "short",
});
setupSettings((saved) => {
  $("sync-since").value = saved.sync_since || "365";
  $("sync-track-sent").checked = !!saved.track_sent;
  refresh();
});
document.addEventListener("click", (event) => {
  const target = event.target.closest("button,a,tr");
  if (!target) return;
  if (target.dataset.category && target.dataset.category !== state.category) {
    state.category = target.dataset.category;
    localStorage.setItem("jobtrack-category", state.category);
    state.status = "all";
    state.search = "";
    $("search").value = "";
    refresh();
  }
  if (target.dataset.page) go(target.dataset.page);
  if (target.dataset.go) go(target.dataset.go);
  if (target.dataset.app) openDetail(target.dataset.app);
  if (target.dataset.list) openList(lists[target.dataset.list]);
  if (target.dataset.week) openWeek(Number(target.dataset.week));
  if (target.dataset.stageList) openStage(target.dataset.stageList);
  if (target.dataset.metric) openMetric(target.dataset.metric);
  if (target.dataset.goal) openGoal(Number(target.dataset.goal));
  if (target.hasAttribute("data-goals-open")) openGoals();
  if (target.dataset.scroll)
    $(target.dataset.scroll)?.scrollIntoView({ behavior: "smooth" });
  if (target.dataset.close) {
    detailVersion++;
    $(target.dataset.close).close();
  }
  if (target.hasAttribute("data-open-sync")) openSync();
  if (target.dataset.filter) {
    state.status = target.dataset.filter;
    renderApplications();
  }
  if (target.hasAttribute("data-reset-filters")) {
    state.status = "all";
    state.search = "";
    $("search").value = "";
    state.asOf = "";
    $("as-of").value = "";
    refresh();
  }
});
document.addEventListener("keydown", (event) => {
  if (
    event.target.matches("tr[data-app]") &&
    ["Enter", " "].includes(event.key)
  ) {
    event.preventDefault();
    openDetail(event.target.dataset.app);
  }
  if (
    event.target.matches("tr[data-list]") &&
    ["Enter", " "].includes(event.key)
  ) {
    event.preventDefault();
    openList(lists[event.target.dataset.list]);
  }
  if (
    event.key === "/" &&
    !event.target.matches("input,textarea,select") &&
    !document.querySelector("dialog[open]")
  ) {
    event.preventDefault();
    go("applications");
    $("search").focus();
  }
});
document.querySelectorAll("dialog").forEach((dialog) => {
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (
      event.clientX < rect.left ||
      event.clientX > rect.right ||
      event.clientY < rect.top ||
      event.clientY > rect.bottom
    )
      dialog.close();
  });
});
$("search").addEventListener("input", () => {
  state.search = $("search").value.trim();
  renderApplications();
});
$("sort").addEventListener("change", () => {
  state.sort = $("sort").value;
  renderApplications();
});
["list", "board"].forEach((view) => {
  $(`${view}-view`).addEventListener("click", () => {
    state.view = view;
    localStorage.setItem("jobtrack-view", view);
    renderApplications();
  });
});
$("as-of").addEventListener("change", () => {
  state.asOf = $("as-of").value;
  refresh();
});
$("back-to-now").addEventListener("click", () => {
  state.asOf = "";
  $("as-of").value = "";
  refresh();
});
$("export-button").addEventListener("click", exportCSV);
$("add-open").addEventListener("click", openAdd);
$("goals-edit").addEventListener("click", openGoals);
$("goals-form").addEventListener("submit", saveGoals);
$("goal-add").addEventListener("click", () => {
  readGoalEditor();
  goalDrafts.push({ metric: "interviews", period: "month", target: 2 });
  renderGoalEditor();
});
$("goals-editor").addEventListener("click", (event) => {
  const remove = event.target.closest("[data-goal-remove]");
  if (!remove) return;
  readGoalEditor();
  goalDrafts.splice(Number(remove.dataset.goalRemove), 1);
  renderGoalEditor();
});
$("add-form").addEventListener("submit", submitAdd);
$("add-company").addEventListener("input", () => {
  clearTimeout(lookupTimer);
  lookupTimer = setTimeout(lookupCompany, 300);
});
$("anonymize").addEventListener("change", () => {
  state.anonymize = $("anonymize").checked;
  renderInsights();
});
$("report-print").addEventListener("click", () => window.print());
$("report-download").addEventListener("click", () => {
  const query = new URLSearchParams({
    category: state.category,
    anonymize: String(state.anonymize),
  });
  const link = document.createElement("a");
  link.href = "/api/report?" + query;
  link.download = "jobtrack-retrospective.md";
  document.body.append(link);
  link.click();
  link.remove();
});
$("application-content").addEventListener("dragstart", (event) => {
  const card = event.target.closest?.(".board-card");
  if (!card) return;
  event.dataTransfer.setData("text/plain", card.dataset.app);
  event.dataTransfer.effectAllowed = "move";
});
$("application-content").addEventListener("dragover", (event) => {
  const column = event.target.closest?.(".board-column");
  if (!column || column.dataset.stage === "ghosted" || state.asOf) return;
  event.preventDefault();
  column.classList.add("drop-target");
});
$("application-content").addEventListener("dragleave", (event) => {
  event.target.closest?.(".board-column")?.classList.remove("drop-target");
});
$("application-content").addEventListener("drop", (event) => {
  const column = event.target.closest?.(".board-column");
  if (!column) return;
  event.preventDefault();
  column.classList.remove("drop-target");
  moveApplication(event.dataTransfer.getData("text/plain"), column.dataset.stage);
});
$("false-positive-open").addEventListener("click", openFalsePositives);
$("false-positive-content").addEventListener("click", (event) => {
  const mark = event.target.closest("[data-restore-mark]");
  if (mark) {
    restoreMark(mark.dataset.restoreMark, mark);
    return;
  }
  const legacy = event.target.closest("[data-restore-legacy]");
  if (legacy) restoreLegacyMark(legacy.dataset.restoreLegacy, legacy);
});
$("mail-tab").addEventListener("click", () => {
  state.activityTab = "mail";
  loadActivity();
});
$("runs-tab").addEventListener("click", () => {
  state.activityTab = "runs";
  loadActivity();
});
["sync-options", "welcome-sync", "connection-sync", "dock-open"].forEach(
  (id) => {
    $(id).addEventListener("click", openSync);
  },
);
$("sync-button").addEventListener("click", () => startSync(false, true));
$("sync-all").addEventListener("click", () => startSync(false, "all"));
$("sync-menu-toggle").addEventListener("click", () => {
  const open = $("sync-menu").hidden;
  $("sync-menu").hidden = !open;
  $("sync-menu-toggle").setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (event) => {
  if (!$("sync-actions").contains(event.target)) closeSyncMenu();
});
$("sync-actions").addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("sync-menu").hidden) {
    closeSyncMenu();
    $("sync-menu-toggle").focus();
  }
});
$("sync-form").addEventListener("submit", (event) => {
  event.preventDefault();
  startSync();
});
$("preview-button").addEventListener("click", () => {
  if ($("sync-form").reportValidity()) startSync(true);
});
$("sync-mode").addEventListener("change", () => {
  const full = $("sync-mode").value === "full";
  $("sync-submit-label").textContent = full ? "Full sync" : "Sync new emails";
  if (full) $("sync-limit").value = "0";
});
window.addEventListener("hashchange", () => go(location.hash.slice(1)));

go(location.hash.slice(1));
refresh();
loadDigest();
api("/api/settings")
  .then((saved) => {
    $("sync-since").value = saved.sync_since || "365";
    $("sync-track-sent").checked = !!saved.track_sent;
  })
  .catch(() => {});
api("/api/health")
  .then((health) => {
    $("connection-label").textContent = health.gmail_token_saved
      ? "Gmail authorized"
      : "Gmail not connected";
    $("connection-dot").classList.toggle("offline", !health.gmail_token_saved);
  })
  .catch(() => {
    $("connection-label").textContent = "Connection unavailable";
    $("connection-dot").classList.add("offline");
  });
api("/api/sync")
  .then((run) => showSync(run, true))
  .catch(() => {});
