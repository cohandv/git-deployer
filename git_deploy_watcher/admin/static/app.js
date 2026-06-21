"use strict";

let currentConfig = null;
let tokenMasked = false;
let activeTab = "general";
/** @type {Array<object>} */
let reposState = [];

const EDITOR_TABS = ["general", "deploy", "telegram", "repos", "environment"];

function $(sel) { return document.querySelector(sel); }
function show(el, text, ok) {
  el.textContent = text;
  el.classList.remove("hidden", "ok", "err");
  el.classList.add(ok ? "ok" : "err");
}
function hide(el) { el.classList.add("hidden"); }

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll("#main-tabs .tab").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tab);
  });

  const isHistory = tab === "history";
  $("#config-form").classList.toggle("hidden", isHistory);
  $("#panel-history").classList.toggle("hidden", !isHistory);

  EDITOR_TABS.forEach((name) => {
    const panel = $(`#panel-${name}`);
    if (panel) panel.classList.toggle("hidden", isHistory || name !== tab);
  });

  if (isHistory) refreshHistorySelectors();
}

function envToRows(container, env) {
  container.innerHTML = "";
  const entries = Object.entries(env || {});
  if (!entries.length) addEnvRow(container, "", "");
  else entries.forEach(([k, v]) => addEnvRow(container, k, v));
}

function addEnvRow(container, key, val) {
  const row = document.createElement("div");
  row.className = "kv-row";
  row.innerHTML =
    `<input type="text" placeholder="KEY" data-env-key value="${escAttr(key)}">` +
    `<input type="text" placeholder="value" data-env-val value="${escAttr(val)}">` +
    `<button type="button" class="danger" data-remove-env title="Remove">×</button>`;
  row.querySelector("[data-remove-env]").onclick = () => row.remove();
  container.appendChild(row);
}

function readEnvTable(container) {
  const out = {};
  container.querySelectorAll(".kv-row").forEach((row) => {
    const k = row.querySelector("[data-env-key]").value.trim();
    const v = row.querySelector("[data-env-val]").value;
    if (k) out[k] = v;
  });
  return out;
}

function escAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function escText(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
}

function deriveNameFromUrl(url) {
  const u = url.trim().replace(/\/$/, "");
  if (!u) return null;
  if (u.toLowerCase().startsWith("ssh://")) {
    const parts = u.split("//", 2)[1] || "";
    const path = parts.includes("/") ? parts.split("/", 2)[1] || "" : parts;
    const base = path.split("/").pop() || "";
    return base.replace(/\.git$/i, "") || null;
  }
  const rest = u.includes(":") ? u.split(":").pop() : u;
  const base = rest.split("/").pop() || "";
  return base.replace(/\.git$/i, "") || null;
}

function repoDisplayName(repo) {
  return repo.name || deriveNameFromUrl(repo.url) || "(unnamed)";
}

function normalizeRepo(raw) {
  return {
    name: raw.name || "",
    url: raw.url || "",
    branch: raw.branch || "main",
    ssh_identity_file: raw.ssh_identity_file || "",
    env: { ...(raw.env || {}) },
    enabled: raw.enabled !== false,
  };
}

function repoToConfigObject(repo) {
  const out = {
    url: repo.url.trim(),
    branch: repo.branch.trim() || "main",
    env: repo.env || {},
  };
  const name = (repo.name || "").trim();
  const ssh = (repo.ssh_identity_file || "").trim();
  if (name) out.name = name;
  if (ssh) out.ssh_identity_file = ssh;
  if (repo.enabled === false) out.enabled = false;
  return out;
}

function renderRepoTable() {
  const tbody = $("#repo-table-body");
  const empty = $("#repo-table-empty");
  const table = $("#repo-table");
  tbody.innerHTML = "";

  if (!reposState.length) {
    table.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }

  table.classList.remove("hidden");
  empty.classList.add("hidden");

  reposState.forEach((repo, index) => {
    const name = repoDisplayName(repo);
    const tr = document.createElement("tr");
    if (!repo.enabled) tr.classList.add("repo-row-disabled");
    tr.innerHTML =
      `<td><strong>${escText(name)}</strong></td>` +
      `<td>${escText(repo.branch)}</td>` +
      `<td class="repo-url-cell" title="${escAttr(repo.url)}">${escText(repo.url)}</td>` +
      `<td><span class="badge ${repo.enabled ? "badge-ok" : "badge-muted"}">${repo.enabled ? "Enabled" : "Disabled"}</span></td>` +
      `<td class="repo-row-actions"></td>`;

    const actions = tr.querySelector(".repo-row-actions");
    actions.appendChild(actionBtn("Edit", () => openRepoDialog(index)));
    actions.appendChild(actionBtn("Deploy", () => deployRepoByName(name, { saveFirst: false })));
    actions.appendChild(actionBtn(repo.enabled ? "Disable" : "Enable", () => toggleRepoEnabled(index)));
    actions.appendChild(actionBtn("Delete", () => confirmDeleteRepo(index), true));

    tbody.appendChild(tr);
  });
}

function actionBtn(label, onclick, danger) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = danger ? "link-btn link-danger" : "link-btn";
  b.textContent = label;
  b.onclick = onclick;
  return b;
}

function openRepoDialog(index) {
  const isCreate = index < 0;
  const repo = isCreate
    ? normalizeRepo({ branch: "main", env: {}, enabled: true })
    : normalizeRepo(reposState[index]);

  $("#repo-dialog-title").textContent = isCreate ? "Add repository" : `Edit ${repoDisplayName(repo)}`;
  $("#repo-dialog-index").value = String(index);
  $("#repo-d-name").value = repo.name;
  $("#repo-d-url").value = repo.url;
  $("#repo-d-branch").value = repo.branch;
  $("#repo-d-ssh").value = repo.ssh_identity_file;
  $("#repo-d-enabled").checked = repo.enabled;
  envToRows($("#repo-d-env"), repo.env);

  $("#repo-dialog").classList.remove("hidden");
  $("#repo-d-url").focus();
}

function closeRepoDialog() {
  $("#repo-dialog").classList.add("hidden");
}

function readRepoDialog() {
  return normalizeRepo({
    name: $("#repo-d-name").value.trim(),
    url: $("#repo-d-url").value.trim(),
    branch: $("#repo-d-branch").value.trim() || "main",
    ssh_identity_file: $("#repo-d-ssh").value.trim(),
    enabled: $("#repo-d-enabled").checked,
    env: readEnvTable($("#repo-d-env")),
  });
}

function saveRepoDialog(ev) {
  ev.preventDefault();
  const repo = readRepoDialog();
  if (!repo.url) {
    show($("#status"), "Repository URL is required", false);
    return;
  }
  const index = parseInt($("#repo-dialog-index").value, 10);
  if (index >= 0) {
    reposState[index] = repo;
  } else {
    reposState.push(repo);
  }
  renderRepoTable();
  closeRepoDialog();
  show($("#status"), "Repository updated in editor — click Save config to persist", true);
}

function toggleRepoEnabled(index) {
  reposState[index].enabled = !reposState[index].enabled;
  renderRepoTable();
  const name = repoDisplayName(reposState[index]);
  show($("#status"), `${name} ${reposState[index].enabled ? "enabled" : "disabled"} — Save config to persist`, true);
}

function confirmDeleteRepo(index) {
  const name = repoDisplayName(reposState[index]);
  if (!window.confirm(`Delete repository "${name}" from config?`)) return;
  reposState.splice(index, 1);
  renderRepoTable();
  show($("#status"), `Removed ${name} — Save config to persist`, true);
}

function fillForm(cfg) {
  const f = $("#config-form");
  f.base_path.value = cfg.base_path || "";
  f.poll_interval_seconds.value = cfg.poll_interval_seconds ?? 60;
  f.state_file.value = cfg.state_file || "";
  f.start_sh_timeout_seconds.value = cfg.start_sh_timeout_seconds ?? 300;
  f.start_sh_failure_retry_attempts.value = cfg.start_sh_failure_retry_attempts ?? 5;
  f.start_sh_failure_retry_interval_seconds.value = cfg.start_sh_failure_retry_interval_seconds ?? 10;
  f.deploy_backoff_initial_seconds.value = cfg.deploy_backoff_initial_seconds ?? 10;
  f.deploy_backoff_max_seconds.value = cfg.deploy_backoff_max_seconds ?? 300;
  f.ssh_identity_file.value = cfg.ssh_identity_file || "";

  envToRows($("#start-sh-env"), cfg.start_sh_env || {});

  const tg = cfg.telegram || {};
  tokenMasked = !!(tg.bot_token);
  f.telegram_bot_token.value = tokenMasked ? "********" : "";
  f.telegram_bot_token.placeholder = tokenMasked ? "leave blank to keep existing" : "optional inline token";
  f.telegram_chat_id.value = tg.chat_id != null ? String(tg.chat_id) : "";
  f.telegram_bot_token_env.value = tg.bot_token_env || "TELEGRAM_BOT_TOKEN";
  f.telegram_chat_id_env.value = tg.chat_id_env || "TELEGRAM_CHAT_ID";

  reposState = (cfg.repos || []).map(normalizeRepo);
  renderRepoTable();
}

function collectConfig() {
  const f = $("#config-form");
  const repos = reposState.map(repoToConfigObject);

  const tg = {
    bot_token_env: f.telegram_bot_token_env.value.trim() || "TELEGRAM_BOT_TOKEN",
    chat_id_env: f.telegram_chat_id_env.value.trim() || "TELEGRAM_CHAT_ID",
  };
  const token = f.telegram_bot_token.value.trim();
  if (token && token !== "********") tg.bot_token = token;
  const chatId = f.telegram_chat_id.value.trim();
  if (chatId) {
    tg.chat_id = /^-?\d+$/.test(chatId) ? parseInt(chatId, 10) : chatId;
  }

  const cfg = {
    config_version: 2,
    base_path: f.base_path.value.trim(),
    poll_interval_seconds: parseInt(f.poll_interval_seconds.value, 10) || 60,
    state_file: f.state_file.value.trim() || "/var/lib/git-deploy-watcher/state.json",
    start_sh_timeout_seconds: parseInt(f.start_sh_timeout_seconds.value, 10) || 300,
    start_sh_failure_retry_attempts: parseInt(f.start_sh_failure_retry_attempts.value, 10) || 5,
    start_sh_failure_retry_interval_seconds: parseInt(f.start_sh_failure_retry_interval_seconds.value, 10) || 0,
    deploy_backoff_initial_seconds: parseInt(f.deploy_backoff_initial_seconds.value, 10) || 10,
    deploy_backoff_max_seconds: parseInt(f.deploy_backoff_max_seconds.value, 10) || 300,
    start_sh_env: readEnvTable($("#start-sh-env")),
    telegram: tg,
    repos,
  };
  const sshGlobal = f.ssh_identity_file.value.trim();
  if (sshGlobal) cfg.ssh_identity_file = sshGlobal;
  return cfg;
}

function showValidationErrors(errors) {
  const box = $("#validation-errors");
  if (!errors || !errors.length) { hide(box); return; }
  box.innerHTML = "<strong>Validation errors</strong><ul>" +
    errors.map((e) => `<li><code>${escAttr(e.path || "(root)")}</code>: ${escAttr(e.message)}</li>`).join("") +
    "</ul>";
  box.classList.remove("hidden");
}

function tabForErrorPath(path) {
  if (!path) return "general";
  if (path.startsWith("telegram")) return "telegram";
  if (path.startsWith("repos")) return "repos";
  if (path.startsWith("start_sh_env")) return "environment";
  if (path.includes("timeout") || path.includes("retry") || path.includes("backoff")) return "deploy";
  return "general";
}

async function loadConfig() {
  const res = await fetch("/api/config");
  const data = await res.json();
  if (!data.config) {
    show($("#status"), data.errors?.[0]?.message || "Failed to load config", false);
    showValidationErrors(data.errors);
    return;
  }
  currentConfig = data.config;
  fillForm(data.config);
  if (!data.validation_ok) {
    show($("#status"), "Config on disk has validation errors", false);
    showValidationErrors(data.errors);
    if (data.errors?.length) switchTab(tabForErrorPath(data.errors[0].path));
  } else {
    hide($("#validation-errors"));
    show($("#status"), `Loaded config v${data.config.config_version || "?"}`, true);
  }
  if (data.warnings?.length) {
    show($("#status"), `Loaded (migration: ${data.warnings.join(", ")})`, true);
  }
  await refreshHistorySelectors();
}

async function deployRepoByName(name, { saveFirst }) {
  if (!name) {
    show($("#status"), "Repository name or URL required", false);
    switchTab("repos");
    return;
  }
  if (saveFirst) {
    const ok = await saveConfig(null, { deploy: [name], silent: true });
    if (!ok) return;
  } else {
    const res = await fetch(`/api/repos/${encodeURIComponent(name)}/deploy`, { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      show($("#status"), `Deploy failed for ${name}`, false);
      showValidationErrors(data.errors);
      return;
    }
  }
  show($("#status"), `Deploy queued for ${name} — pull + start.sh runs shortly`, true);
}

async function saveConfig(ev, opts = {}) {
  if (ev) ev.preventDefault();
  if (!reposState.length) {
    show($("#status"), "Add at least one repository", false);
    switchTab("repos");
    return false;
  }
  const cfg = collectConfig();
  let url = "/api/config";
  const deployList = opts.deploy || [];
  if ($("#deploy-all-after-save")?.checked) {
    cfg.repos.forEach((r) => {
      if (r.enabled === false) return;
      const n = r.name || deriveNameFromUrl(r.url);
      if (n) deployList.push(n);
    });
  }
  if (deployList.length) {
    url += "?" + [...new Set(deployList)].map((n) => `deploy=${encodeURIComponent(n)}`).join("&");
  }
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
  const data = await res.json();
  if (!data.ok) {
    if (!opts.silent) show($("#status"), "Save failed", false);
    showValidationErrors(data.errors);
    if (data.errors?.length) switchTab(tabForErrorPath(data.errors[0].path));
    return false;
  }
  hide($("#validation-errors"));
  let msg = `Saved config v${data.config_version}`;
  if (data.deploy_queued?.length) {
    msg += ` — deploy queued: ${data.deploy_queued.join(", ")}`;
  }
  if (!opts.silent) show($("#status"), msg, true);
  await loadConfig();
  return true;
}

async function refreshHistorySelectors() {
  const res = await fetch("/api/history");
  const data = await res.json();
  const fromSel = $("#diff-from");
  const toSel = $("#diff-to");
  fromSel.innerHTML = "";
  toSel.innerHTML = '<option value="current">current</option>';
  (data.history || []).forEach((h) => {
    fromSel.appendChild(new Option(h.id, h.id));
    toSel.appendChild(new Option(h.id, h.id));
  });
}

async function runDiff() {
  const fromId = $("#diff-from").value;
  const toId = $("#diff-to").value;
  if (!fromId) return;
  const res = await fetch(`/api/diff?from=${encodeURIComponent(fromId)}&to=${encodeURIComponent(toId)}`);
  const data = await res.json();
  $("#diff-output").textContent = data.diff || data.errors?.[0]?.message || "(no diff)";
}

document.querySelectorAll("#main-tabs .tab").forEach((btn) => {
  btn.onclick = () => switchTab(btn.dataset.tab);
});

$("#add-global-env").onclick = () => addEnvRow($("#start-sh-env"), "", "");
$("#create-repo-btn").onclick = () => openRepoDialog(-1);
$("#repo-dialog-form").onsubmit = saveRepoDialog;
$("#repo-d-add-env").onclick = () => addEnvRow($("#repo-d-env"), "", "");
document.querySelectorAll("[data-close-dialog]").forEach((el) => {
  el.onclick = closeRepoDialog;
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#repo-dialog").classList.contains("hidden")) closeRepoDialog();
});

$("#config-form").onsubmit = saveConfig;
$("#reload-btn").onclick = () => loadConfig();
$("#run-diff").onclick = runDiff;

switchTab("general");
loadConfig();
