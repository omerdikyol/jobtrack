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
  status: "all",
  search: "",
  sort: "recent",
  view: localStorage.getItem("jobtrack-view") || "list",
  asOf: "",
  activityTab: "mail",
};
let refreshVersion = 0,
  activityVersion = 0,
  detailVersion = 0,
  syncTimer,
  lastSyncState = null,
  pollFailures = 0;
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
function go(page) {
  page = ["overview", "applications", "activity"].includes(page)
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
}
async function refresh() {
  const version = ++refreshVersion;
  try {
    const [applications, overview] = await Promise.all([
      api("/api/applications" + categorySuffix()),
      api("/api/overview" + categorySuffix()),
    ]);
    if (version !== refreshVersion) return;
    state.applications = applications.applications;
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
    notice("");
  } catch (error) {
    if (version === refreshVersion)
      notice(`Could not load your workspace: ${error.message}`);
  }
}
function renderOverview() {
  const data = state.overview;
  const metrics = [
    {
      label: "Total applications",
      value: data.total,
      foot: "Tracked in this category",
      icon: "applications",
    },
    {
      label: "In progress",
      value: data.active,
      foot: "Open applications",
      icon: "clock",
    },
    {
      label: "Interviewing",
      value: data.interviewing,
      foot: `${data.statuses.assessment || 0} more at the assessment stage`,
      icon: "interview",
    },
    {
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
        `<div class="metric"><div class="metric-label">${m.label}${icon(m.icon)}</div><div class="metric-value">${m.value}${m.suffix ? `<small>${m.suffix}</small>` : ""}</div><div class="metric-foot">${m.foot}</div></div>`,
    )
    .join("");
  $("welcome").hidden = data.total > 0;
  $("last-sync").textContent = data.last_sync
    ? `Last synced ${relative(data.last_sync).toLowerCase()}`
    : "Ready for your first sync";
  const total = data.weeks.reduce((sum, w) => sum + w.applications, 0),
    max = Math.max(1, ...data.weeks.map((w) => w.applications));
  $("chart-total").textContent = total;
  $("weekly-chart").setAttribute(
    "aria-label",
    data.weeks
      .map(
        (w) =>
          `Week of ${shortDate(w.week + "T12:00:00")}: ${w.applications} applications`,
      )
      .join(". "),
  );
  $("weekly-chart").innerHTML = data.weeks
    .map(
      (w) =>
        `<div class="chart-column"><div class="chart-bar" style="height:${w.applications ? Math.max(8, (w.applications / max) * 78) : 2}%" title="${e(dateOnly(w.week))}: ${w.applications} applications">${w.applications ? `<span class="chart-number">${w.applications}</span>` : ""}</div><div class="chart-label">${e(dateOnly(w.week))}</div></div>`,
    )
    .join("");
  const pipelineStages = [
    "applied",
    "assessment",
    "interview",
    "offer",
    "rejected",
  ];
  if (data.statuses.outreach) pipelineStages.unshift("outreach");
  if (data.statuses.incomplete) pipelineStages.unshift("incomplete");
  $("pipeline").innerHTML = pipelineStages
    .map(
      (kind) =>
        `<div class="pipeline-row" style="--stage-color:var(--${kind})"><span class="pipeline-name"><span class="status-dot"></span>${labels[kind]}</span><div class="pipeline-track"><div class="pipeline-fill" style="width:${data.total ? ((data.statuses[kind] || 0) / data.total) * 100 : 0}%"></div></div><span class="pipeline-count">${data.statuses[kind] || 0}</span></div>`,
    )
    .join("");
  $("role-categories").innerHTML = data.role_categories?.length
    ? data.role_categories
        .map(
          (r, i) =>
            `<div class="role-row"><span class="role-name"><span class="status-dot" style="--stage-color:var(--role-${i % 8})"></span><span class="role-label">${e(r.name)}</span></span><div class="role-track"><div class="role-fill" style="--stage-color:var(--role-${i % 8});width:${data.total ? (r.count / data.total) * 100 : 0}%"></div></div><span class="role-count">${r.count}</span></div>`,
        )
        .join("")
    : panelEmpty(
        "No roles yet",
        "Role titles appear here once applications are imported.",
        "applications",
      );
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
    ["interview", "Interviewing", counts.interview || 0],
    ["offer", "Offers", counts.offer || 0],
    ["rejected", "Rejected", counts.rejected || 0],
  ];
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
        return `<section class="board-column"><h2 class="board-column-heading" style="--stage-color:var(--${kind})"><span class="status-dot"></span>${labels[kind]}<span class="count-bubble">${items.length}</span></h2>${items.length ? items.map((a) => `<button class="board-card" data-app="${a.id}"><span class="board-card-head">${avatar(a.company)}<strong>${e(a.company)}</strong></span><p>${e(a.role || "Role not stated")}</p><span class="board-card-foot"><span>${e(relative(a.last_event_at, reference()))}</span><span>${a.event_count} mail${a.event_count === 1 ? "" : "s"}</span></span>${a.follow_up_on ? `<small class="follow-up-summary">Follow up ${e(dateOnly(a.follow_up_on))}</small>` : ""}</button>`).join("") : '<p class="board-empty">No applications at this stage</p>'}</section>`;
      })
      .join("")}</div>`;
    return;
  }
  $("application-content").innerHTML =
    `<table class="application-table"><thead><tr><th scope="col">COMPANY & ROLE</th><th scope="col">STAGE</th><th scope="col">LAST ACTIVITY</th><th scope="col">MESSAGES</th><th scope="col">FOLLOW-UP</th></tr></thead><tbody>${apps.map((a) => `<tr tabindex="0" data-app="${a.id}" aria-label="${e(a.company)}, ${e(a.role || "role not stated")}, ${e(labels[a.status])}"><td><div class="company-cell">${avatar(a.company)}<div><strong>${e(a.company)}</strong><small>${e(a.role || "Role not stated")}</small></div></div></td><td>${badge(a.status)}</td><td class="num">${e(relative(a.last_event_at, reference()))}<span class="date-caption">${e(fullDate(a.last_event_at))}</span></td><td class="num"><span class="message-count">${icon("mail")}${a.event_count}</span></td><td><span class="followup-date ${a.follow_up_on && a.follow_up_on <= new Date().toLocaleDateString("en-CA") ? "due" : ""}">${e(dateOnly(a.follow_up_on))}</span></td></tr>`).join("")}</tbody></table>`;
}
async function openDetail(id) {
  const version = ++detailVersion;
  try {
    const { application: a, events } = await api(
      `/api/applications/${id}${historySuffix()}`,
    );
    if (version !== detailVersion) return;
    const latestURL = [...events]
      .reverse()
      .find((ev) => ev.gmail_url)?.gmail_url;
    $("detail-content").innerHTML =
      `<div class="detail-top"><span class="eyebrow">${a.category === "freelance" ? "FREELANCE PROJECT" : "EMPLOYMENT APPLICATION"} · #${a.id}</span><button class="button icon-button" data-close="detail-dialog" aria-label="Close application details">${icon("close")}</button></div><div class="detail-head"><div class="detail-company">${avatar(a.company)}<div><h2 id="detail-title">${e(a.company)}</h2><p>${e(a.role || "Role not stated")}</p></div></div><div class="detail-head-meta">${badge(a.status)}<span>First seen ${e(fullDate(a.first_seen))}</span>${gmailLink(latestURL)}</div></div>${state.asOf ? `<div class="snapshot-detail">Email history as of ${e(dateOnly(state.asOf))}. Return to today to edit this application.</div>` : `<section class="detail-section"><details class="detail-edit"><summary>Notes & application details</summary><form id="application-edit"><div class="form-grid"><label>Company<input id="edit-company" value="${e(a.company)}" required maxlength="200"></label><label>Role<input id="edit-role" value="${e(a.role)}" maxlength="300" placeholder="Role not stated"></label></div><label>Work category<select id="edit-category"><option value="employment" ${a.category === "employment" ? "selected" : ""}>Employment</option><option value="freelance" ${a.category === "freelance" ? "selected" : ""}>Freelance</option></select></label><div class="form-grid"><label>Status correction<select id="edit-status"><option value="">Automatic · from email</option>${stages.map((k) => `<option value="${k}" ${a.status_override === k ? "selected" : ""}>${labels[k]}</option>`).join("")}</select></label><label>Follow-up date<input id="edit-followup" value="${e(a.follow_up_on)}" type="date"></label></div><label>Your notes<textarea id="edit-notes" aria-label="Your notes" maxlength="20000" placeholder="Contacts, interview notes, and next actions.">${e(a.notes)}</textarea></label><div id="edit-result" class="form-result" hidden role="status"></div><button class="button button-primary" type="submit">Save application ${icon("check")}</button></form></details>${a.notes ? `<div class="notes-preview">${e(a.notes)}</div>` : ""}${a.follow_up_on ? `<div class="follow-up-summary">${icon("calendar")}Follow up ${e(dateOnly(a.follow_up_on))}</div>` : ""}${a.status_override ? '<div class="follow-up-summary">Status corrected by you. Choose Automatic to use email history again.</div>' : ""}</section>`}<section class="detail-section"><h3>Email timeline <span class="count-bubble">${events.length}</span></h3><ol class="timeline">${[
        ...events,
      ]
        .reverse()
        .map(
          (ev) =>
            `<li class="timeline-event" style="--stage-color:var(--${stages.includes(ev.kind) ? ev.kind : "applied"})"><div class="timeline-head">${badge(ev.kind)}<time datetime="${e(ev.date)}">${e(new Date(ev.date).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" }))}</time></div><h4>${e(ev.subject || "(No subject)")}</h4><div class="timeline-sender">${e(ev.sender)}</div>${ev.snippet ? `<p class="timeline-snippet">${e(ev.snippet)}</p>` : ""}${gmailLink(ev.gmail_url)}<details class="classification"><summary>Classification details</summary><p>${ev.source === "llm" ? "Model analysis" : "Pattern rules"} · ${Math.round(ev.confidence * 100)}% confidence</p>${reviewHTML(ev.review)}</details></li>`,
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
  } catch (error) {
    notice(`Could not open that application: ${error.message}`);
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
      ? { since: "0", limit: 0, recheck: true }
      : { limit: 100, recheck: false }
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

hydrateIcons();
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const label =
    theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  $("theme-toggle").innerHTML = icon(theme === "dark" ? "sun" : "moon");
  $("theme-toggle").setAttribute("aria-label", label);
  $("theme-toggle").title = label;
  document.querySelector('meta[name="theme-color"]').content =
    theme === "dark" ? "#0a0b0d" : "#f6f3ec";
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
