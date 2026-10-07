export const $ = (id) => document.getElementById(id);
export const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  moon: '<path d="M20.5 13a8.5 8.5 0 0 1-9.5-9.5A8.5 8.5 0 1 0 20.5 13Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  freelance: '<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18"/>',
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  applications:
    '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v3M3 12h18M10 12v3h4v-3"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  settings:
    '<path d="m10 3-.6 2.1-2 .9-2.1-.5-2 3.5 1.5 1.6v2.8l-1.5 1.6 2 3.5 2.1-.5 2 .9.6 2.1h4l.6-2.1 2-.9 2.1.5 2-3.5-1.5-1.6v-2.8l1.5-1.6-2-3.5-2.1.5-2-.9L14 3z"/><circle cx="12" cy="12" r="3"/>',
  shield:
    '<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  sync: '<path d="M20 7a8 8 0 0 0-14-2L3 8m0-5v5h5M4 17a8 8 0 0 0 14 2l3-3m0 5v-5h-5"/>',
  chevron: '<path d="m7 10 5 5 5-5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  arrow: '<path d="M4 12h16m-5-5 5 5-5 5"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  pipeline: '<path d="M3 5h18M6 12h12M9 19h6"/>',
  spark:
    '<path d="m12 3 2.8 6.2L21 12l-6.2 2.8L12 21l-2.8-6.2L3 12l6.2-2.8zM20 2v4m-2-2h4"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 15v5h16v-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  list: '<path d="M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1"/>',
  board:
    '<rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="12" rx="1"/><rect x="17" y="4" width="4" height="8" rx="1"/>',
  sort: '<path d="M4 6h16M7 12h10M10 18h4"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  calendar:
    '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18m-13 5h2m4 0h2"/>',
  interview:
    '<path d="M21 11.5a8.5 8.5 0 0 1-9 8.5c-1.5 0-3-.4-4.5-1.2L3 21l1.5-5.3A8.5 8.5 0 1 1 21 11.5Z"/><path d="M8 11h8M8 14h5"/>',
  offer:
    '<path d="m12 3 2.8 5.7L21 9.6l-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};
export const icon = (name) =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.mail}</svg>`;
export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = icon(el.dataset.icon);
  });
}
export const stages = [
  "incomplete",
  "outreach",
  "applied",
  "assessment",
  "interview",
  "offer",
  "rejected",
];
export const labels = {
  incomplete: "Incomplete",
  outreach: "Outreach",
  applied: "Applied",
  assessment: "Assessment",
  interview: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
};
const tones = {
  applied: ["#edf2f6", "#657f94", "#dfe8ee"],
  assessment: ["#faf3e6", "#a08043", "#eee0c5"],
  interview: ["#eaf3ed", "#527d65", "#d8e8dc"],
  offer: ["#eff5e5", "#728d45", "#e0eacb"],
  rejected: ["#f8eeeb", "#a68075", "#eeded8"],
  outreach: ["#f1eef6", "#9681aa", "#e4ddeb"],
  incomplete: ["#f5f2e9", "#9e967c", "#e9e2d3"],
};
export function badge(kind) {
  kind = stages.includes(kind) ? kind : "applied";
  const [bg, ink, border] = tones[kind];
  return `<span class="badge" style="--badge-bg:${bg};--badge-ink:${ink};--badge-border:${border};--stage-color:var(--${kind})"><span class="status-dot"></span>${labels[kind]}</span>`;
}
export const shortDate = (iso) =>
  new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
export const fullDate = (iso) =>
  new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
export function relative(iso, reference = new Date()) {
  const seconds = Math.max(0, (reference - new Date(iso)) / 1000);
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  const days = Math.floor(seconds / 86400);
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}
const avatarTones = [
  ["#edf2e7", "#7b925f"],
  ["#f3eeeb", "#a08978"],
  ["#eceff5", "#8597ad"],
  ["#f1edf5", "#9b88ac"],
  ["#f5f2e8", "#a2997a"],
];
export function avatar(company) {
  const sum = [...String(company)].reduce((n, c) => n + c.codePointAt(0), 0);
  const [bg, ink] = avatarTones[sum % avatarTones.length];
  const initials = String(company)
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => [...w][0] || "")
    .join("")
    .toUpperCase();
  return `<span class="company-avatar" style="--avatar-bg:${bg};--avatar-ink:${ink}" aria-hidden="true">${escapeHTML(initials)}</span>`;
}
export function panelEmpty(title, description, name = "mail") {
  return `<div class="panel-empty"><span data-icon="${name}">${icon(name)}</span><strong>${escapeHTML(title)}</strong><p>${escapeHTML(description)}</p></div>`;
}
let toastTimer;
export function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 4000);
}
export function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}
export function gmailLink(url, label = "Open in Gmail ↗") {
  if (
    !/^https:\/\/mail\.google\.com\/mail\/u\/\d+\/#all\/[A-Za-z0-9_-]+$/.test(
      url || "",
    )
  )
    return "";
  return `<a class="text-button" href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(label)}</a>`;
}
