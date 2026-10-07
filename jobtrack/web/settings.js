import { api, write } from "./api.js";
import { $, escapeHTML as e, toast, icon } from "./ui.js";
import { reviewHTML } from "./reviews.js";
let current,
  provider,
  tab = "connections",
  team = [],
  strategy = "single",
  rounds = 2;
let drafts = {},
  catalogs = {},
  searches = {},
  busy = false;
const name = (p) =>
  p === "nvidia"
    ? "NVIDIA NIM"
    : p === "ollama"
      ? "Ollama"
      : p === "openai"
        ? "OpenAI"
        : p === "openrouter"
          ? "OpenRouter"
          : p[0].toUpperCase() + p.slice(1);
const initials = (p) =>
  p === "nvidia" ? "NV" : p === "ollama" ? "OL" : p.slice(0, 2).toUpperCase();
const accessNote = (p) =>
  p === "openrouter"
    ? "Free variants only · live provider pricing"
    : p === "nvidia"
      ? "Developer API access · trial limits apply"
      : ["groq", "cerebras", "gemini"].includes(p)
        ? "Free tier or trial depends on your account · quotas apply"
        : p === "ollama"
          ? "Local models · no API usage charges"
          : "Provider pricing applies";
function result(message, error = false) {
  $("settings-result").hidden = false;
  $("settings-result").textContent = message;
  $("settings-result").classList.toggle("error", error);
}
function capture() {
  if ($("s-url")) {
    const draft = (drafts[provider] ||= {});
    if ($("s-key")?.value.trim()) draft.api_key = $("s-key").value.trim();
    else if (draft.api_key) delete draft.api_key;
    draft.base_url = $("s-url").value.trim();
  }
}
function selected(p, m) {
  return team.some((t) => t.provider === p && t.model === m);
}
function render() {
  $("connection-count").textContent = Object.values(current.connections).filter(
    (c) => c.api_key_set || c.is_local,
  ).length;
  $("team-count").textContent = team.length;
  document
    .querySelectorAll(".settings-tabs [data-settings-tab]")
    .forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.settingsTab === tab));
      b.tabIndex = b.dataset.settingsTab === tab ? 0 : -1;
    });
  $("settings-panel").setAttribute(
    "aria-labelledby",
    `${tab === "connections" ? "connections" : "team"}-tab`,
  );
  const conn = current.connections[provider];
  const draft = drafts[provider] || {};
  const url = draft.base_url ?? conn.base_url;
  const search = searches[provider] || "";
  const models = [
    ...new Set([
      ...(catalogs[provider] || []),
      ...team.filter((t) => t.provider === provider).map((t) => t.model),
    ]),
  ];
  const rail = `<nav class="provider-rail" aria-label="Model providers">${current.providers.map((p) => `<button type="button" data-provider="${e(p)}" aria-pressed="${p === provider}"><span class="provider-mark">${e(initials(p))}</span><span><strong>${e(name(p))}</strong><small>${current.connections[p].is_local ? "Local connection" : current.connections[p].api_key_set ? "Key configured" : "Add API key"}</small></span><span class="connection-dot ${current.connections[p].api_key_set || current.connections[p].is_local ? "configured" : ""}"></span></button>`).join("")}</nav>`;
  const notice = `<p class="field-hint">${e(accessNote(provider))}</p><div class="provider-note">${conn.is_local ? "Local endpoint · email content stays on this device." : `Selected emails and related context are sent to ${e(name(provider))} at the configured endpoint when analysis is enabled.`}</div>`;
  if (tab === "connections") {
    $("settings-panel").innerHTML =
      `<div class="settings-workspace">${rail}<section class="connection-editor"><div class="settings-section-head"><span class="provider-mark large">${e(initials(provider))}</span><div><h3>${e(name(provider))}</h3><p>${provider === "ollama" ? "Your local model server. No API key needed." : "One connection for every model from this provider."}</p></div></div>${provider !== "ollama" ? `<label>API key<div class="secret-input"><input id="s-key" type="password" autocomplete="new-password" spellcheck="false" value="${e(draft.api_key || "")}" placeholder="${conn.api_key_set ? "Enter a replacement key" : "Paste your API key"}"><button type="button" id="key-reveal" aria-label="Show entered API key">${icon("eye")}</button></div></label><div class="key-line"><span id="key-state">${draft.api_key === "" ? "Saved key will be removed on save" : conn.api_key_set ? `${e(conn.api_key_hint)} · ${e(conn.api_key_source)}` : "No key configured"}</span>${conn.api_key_source === "settings" ? '<button class="text-button" id="key-clear" type="button">Remove saved key</button>' : ""}</div><p class="field-hint">Keys from .env are read on demand. Keys entered here override .env and are saved privately.</p>` : ""}<label>Endpoint<input id="s-url" type="url" value="${e(url)}" spellcheck="false"></label>${notice}<div class="connection-actions"><button class="button button-primary" type="button" id="connection-save">Save connection</button><button class="button" type="button" id="model-refresh">Save & discover models ↻</button></div><div class="connection-next"><span>${icon("spark")}</span><div><strong>Choose your reviewers</strong><p>Find available models here, then add them to your review team.</p></div><button class="text-button" type="button" data-settings-tab="team">Build team →</button></div></section></div>`;
  } else {
    $("settings-panel").innerHTML =
      `<div class="team-strategy"><div><h3>How should your team decide?</h3><p>Each reviewer checks the same email and its related history.</p></div><div class="strategy-switch"><button type="button" data-strategy="single" aria-pressed="${strategy === "single"}">Single model</button><button type="button" data-strategy="consensus" aria-pressed="${strategy === "consensus"}">Consensus</button></div></div><div class="review-flow"><div><span>01</span><strong>Read independently</strong><p>Selected models review in parallel.</p></div><div><span>02</span><strong>Compare evidence</strong><p>Reviewers see peer verdicts and reconsider.</p></div><div><span>03</span><strong>Agree or flag</strong><p>Unanimous agreement, or manual review.</p></div></div><div class="settings-workspace model-workspace">${rail}<section class="model-picker"><div class="settings-section-head"><div><h3>${e(name(provider))} models</h3><p>Choose up to five across your providers.</p></div><button type="button" class="text-button" id="model-refresh">Discover ↻</button></div><label class="model-search">Search models<input id="model-search" type="search" value="${e(search)}" placeholder="Filter available model IDs" autocomplete="off"></label><div class="model-catalog" id="model-catalog">${models.length ? models.map((m) => `<label class="model-option" data-model-name="${e(m.toLowerCase())}" ${m.toLowerCase().includes(search.toLowerCase()) ? "" : "hidden"}><input type="checkbox" data-model="${e(m)}" ${selected(provider, m) ? "checked" : ""}><span><strong>${e(m)}</strong><small>${e(name(provider))} · ${provider === "openrouter" ? "free" : conn.is_local ? "local" : "hosted"}</small></span></label>`).join("") : '<div class="catalog-empty">Discover models from your saved connection,<br>or enter a supported model ID below.</div>'}</div><div class="custom-model"><label>Custom model ID<input id="custom-model" placeholder="Exact provider model ID" maxlength="200" autocomplete="off"></label><button class="button" type="button" id="model-add" aria-label="Add custom model">Add +</button></div>${notice}</section></div><section class="selected-team"><div class="settings-section-head"><div><h3>Your reviewers <span class="count-bubble">${team.length}/5</span></h3><p>${strategy === "consensus" ? "At least two distinct models. All must agree on the event, company, and role." : "The first model is your primary reviewer. Other selections are kept for consensus."}</p></div>${strategy === "consensus" ? `<label>Discussion limit<select id="review-rounds">${[1, 2, 3].map((n) => `<option value="${n}" ${rounds === n ? "selected" : ""}>${n} ${n === 1 ? "round" : "rounds"}</option>`).join("")}</select></label>` : ""}</div><div class="team-members">${team.map((t, i) => `<div class="team-member"><span class="provider-mark">${e(initials(t.provider))}</span><div><strong>${e(t.model)}</strong><small>${e(name(t.provider))}${strategy === "single" && i === 0 ? " · primary" : ""}</small></div>${strategy === "single" && i > 0 ? `<button class="text-button" type="button" data-primary="${i}">Make primary</button>` : ""}<button class="button icon-button" type="button" data-remove="${i}" aria-label="Remove ${e(t.model)}">${icon("close")}</button></div>`).join("") || '<p class="catalog-empty">Select your first reviewer above.</p>'}</div><p class="field-hint">${strategy === "consensus" ? `One independent review + up to ${rounds} discussion rounds · each round limited to 45 seconds. Multiple hosted models increase usage and may incur provider charges.` : "Single model uses one reviewer per analyzed email."}</p></section><details class="recent-decisions"><summary>Recent consensus decisions</summary><button type="button" class="text-button" id="reviews-refresh">Load recent decisions ↻</button><div id="recent-reviews"></div></details>`;
  }
}
async function load() {
  current = await api("/api/settings");
  provider = current.llm_provider;
  team = current.review_models.map((t) => ({ ...t }));
  strategy = current.review_strategy;
  rounds = current.review_rounds;
  drafts = {};
  catalogs = Object.fromEntries(
    current.providers.map((p) => [
      p,
      team.filter((t) => t.provider === p).map((t) => t.model),
    ]),
  );
  searches = {};
  $("s-mode").value = current.llm_mode;
  $("s-since").value = current.sync_since;
  $("s-track-sent").checked = !!current.track_sent;
  $("team-test-report").replaceChildren();
  render();
}
function setBusy(value) {
  busy = value;
  $("settings-form")
    .querySelectorAll("button,input,select")
    .forEach((el) => (el.disabled = value));
}
async function saveConnection() {
  capture();
  current = await write(
    "/api/settings",
    { connections: { [provider]: drafts[provider] || {} } },
    "PUT",
  );
  delete drafts[provider];
}
async function save() {
  capture();
  if (!team.length) throw new Error("Select at least one reviewer.");
  if (strategy === "consensus" && team.length < 2)
    throw new Error("Consensus needs at least two distinct models.");
  const primary = team[0];
  current = await write(
    "/api/settings",
    {
      connections: drafts,
      review_models: team,
      review_strategy: strategy,
      review_rounds: rounds,
      llm_provider: primary.provider,
      llm_model: primary.model,
      llm_base_url:
        drafts[primary.provider]?.base_url ??
        current.connections[primary.provider].base_url,
      llm_mode: $("s-mode").value,
      sync_since: $("s-since").value,
      track_sent: $("s-track-sent").checked,
    },
    "PUT",
  );
  drafts = {};
  render();
  return current;
}
function addModel(model) {
  model = model.trim();
  if (!model || selected(provider, model)) return;
  if (team.length >= 5) throw new Error("Choose up to five reviewers.");
  team.push({ provider, model });
  render();
}
export function setupSettings(onSave) {
  $("settings-button").addEventListener("click", async () => {
    $("settings-result").hidden = true;
    try {
      await load();
      $("settings-dialog").showModal();
    } catch (error) {
      toast(error.message);
    }
  });
  $("settings-form").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button || busy || button.type === "submit") return;
    try {
      if (button.dataset.settingsTab) {
        capture();
        tab = button.dataset.settingsTab;
        render();
      } else if (button.dataset.provider) {
        capture();
        provider = button.dataset.provider;
        render();
      } else if (button.dataset.strategy) {
        strategy = button.dataset.strategy;
        render();
      } else if (button.hasAttribute("data-remove")) {
        team.splice(Number(button.dataset.remove), 1);
        render();
      } else if (button.hasAttribute("data-primary")) {
        const [primary] = team.splice(Number(button.dataset.primary), 1);
        team.unshift(primary);
        render();
      } else if (button.id === "model-add") {
        addModel($("custom-model").value);
      } else if (button.id === "key-reveal") {
        const input = $("s-key");
        input.type = input.type === "password" ? "text" : "password";
        button.setAttribute(
          "aria-label",
          input.type === "password"
            ? "Show entered API key"
            : "Hide entered API key",
        );
      } else if (button.id === "key-clear") {
        capture();
        drafts[provider].api_key = "";
        render();
      } else if (
        button.id === "connection-save" ||
        button.id === "model-refresh"
      ) {
        setBusy(true);
        result(
          button.id === "model-refresh"
            ? "Discovering models from your connection…"
            : "Saving connection…",
        );
        await saveConnection();
        if (button.id === "model-refresh") {
          const response = await api(
            `/api/settings/models?provider=${encodeURIComponent(provider)}`,
          );
          catalogs[provider] = response.models;
          result(
            response.models.length
              ? `${response.models.length} current text models. Open Review team to select them.`
              : "No models returned. Check the connection or enter a supported model ID.",
          );
        } else result(`${name(provider)} connection saved.`);
        render();
      } else if (button.id === "test-model") {
        setBusy(true);
        result("Saving and testing your team with a sample email…");
        const saved = await save();
        onSave(saved);
        setBusy(true);
        const test = await write("/api/settings/test", {});
        result(
          test.ok
            ? `Team ready. Sample classified as ${test.sample.event} for ${test.sample.company || "the sample company"}.`
            : test.error,
          !test.ok,
        );
        $("team-test-report").innerHTML = reviewHTML(test.review);
      } else if (button.id === "reviews-refresh") {
        setBusy(true);
        const response = await api("/api/reviews");
        $("recent-reviews").innerHTML = response.reviews.length
          ? response.reviews
              .map(
                (r) =>
                  `<details class="saved-review"><summary>${e(r.subject)} · ${e(new Date(r.event_date).toLocaleString())} · ${r.status === "agreed" ? "Agreed" : "Needs review"}</summary>${reviewHTML(r.review)}</details>`,
              )
              .join("")
          : '<p class="field-hint">Consensus decisions will appear after your next sync.</p>';
      }
    } catch (error) {
      result(error.message, true);
    } finally {
      if (busy) setBusy(false);
    }
  });
  $("settings-form").addEventListener("change", (event) => {
    if (event.target.id === "review-rounds") {
      rounds = Number(event.target.value);
      render();
    } else if (event.target.hasAttribute("data-model")) {
      const model = event.target.dataset.model;
      try {
        if (event.target.checked) addModel(model);
        else {
          team = team.filter(
            (t) => !(t.provider === provider && t.model === model),
          );
          render();
        }
      } catch (error) {
        event.target.checked = false;
        result(error.message, true);
      }
    }
  });
  $("settings-form").addEventListener("input", (event) => {
    if (event.target.id === "model-search") {
      searches[provider] = event.target.value;
      const search = event.target.value.toLowerCase();
      document
        .querySelectorAll("[data-model-name]")
        .forEach((el) => (el.hidden = !el.dataset.modelName.includes(search)));
    }
  });
  $("settings-form").addEventListener("keydown", (event) => {
    if (event.target.id === "custom-model" && event.key === "Enter") {
      event.preventDefault();
      try {
        addModel(event.target.value);
      } catch (error) {
        result(error.message, true);
      }
    }
    if (
      event.target.getAttribute("role") === "tab" &&
      ["ArrowLeft", "ArrowRight"].includes(event.key)
    ) {
      event.preventDefault();
      capture();
      tab = tab === "connections" ? "team" : "connections";
      render();
      $(tab === "team" ? "team-tab" : "connections-tab").focus();
    }
  });
  $("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const saved = await save();
      onSave(saved);
      result("Settings saved. Your team will be used on the next sync.");
      toast("Workspace settings saved");
    } catch (error) {
      result(error.message, true);
    } finally {
      setBusy(false);
    }
  });
}
