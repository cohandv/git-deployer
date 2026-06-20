"use strict";

let currentConfig = null;
let tokenMasked = false;

function $(sel) { return document.querySelector(sel); }
function show(el, text, ok) {
  el.textContent = text;
  el.classList.remove("hidden", "ok", "err");
  el.classList.add(ok ? "ok" : "err");
}
function hide(el) { el.classList.add("hidden"); }

function envToRows(container, env, prefix) {
  container.innerHTML = "";
  const entries = Object.entries(env || {});
  if (!entries.length) addEnvRow(container, "", "", prefix);
  else entries.forEach(([k, v]) => addEnvRow(container, k, v, prefix));
}

function addEnvRow(container, key, val, prefix) {
  const row = document.createElement("div");
  row.className = "kv-row";
  row.innerHTML =
    `<input type="text" placeholder="KEY" data-env-key value="${escAttr(key)}">` +
    `<input type="text" placeholder="value" data-env-val value="${escAttr(val)}">` +
    `<button type="button" class="danger" data-remove-env>×</button>`;
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

function renderRepo(repo, index) {
  const card = document.createElement("div");
  card.className = "repo-card";
  card.dataset.index = String(index);
  const title = repo.name || `repo ${index + 1}`;
  card.innerHTML = `<h3><span>${escAttr(title)}</span><button type="button" class="danger" data-remove-repo>Remove</button></h3>` +
    `<label>Name <input data-field="name" value="${escAttr(repo.name || "")}"></label>` +
    `<label>URL (SSH) <input data-field="url" required value="${escAttr(repo.url || "")}"></label>` +
    `<label>Branch <input data-field="branch" required value="${escAttr(repo.branch || "")}"></label>` +
    `<label>SSH identity <input data-field="ssh_identity_file" value="${escAttr(repo.ssh_identity_file || "")}"></label>` +
    `<p class="subtitle">Per-repo start.sh environment</p>` +
    `<div class="kv-table" data-repo-env></div>` +
    `<button type="button" class="secondary" data-add-repo-env>Add variable</button>`;
  card.querySelector("[data-remove-repo]").onclick = () => card.remove();
  const envBox = card.querySelector("[data-repo-env]");
  envToRows(envBox, repo.env || {});
  card.querySelector("[data-add-repo-env]").onclick = () => addEnvRow(envBox, "", "");
  return card;
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
  f.telegram_bot_token.placeholder = tokenMasked ? "leave blank to keep" : "optional inline token";
  f.telegram_chat_id.value = tg.chat_id != null ? String(tg.chat_id) : "";
  f.telegram_bot_token_env.value = tg.bot_token_env || "TELEGRAM_BOT_TOKEN";
  f.telegram_chat_id_env.value = tg.chat_id_env || "TELEGRAM_CHAT_ID";

  const reposEl = $("#repos");
  reposEl.innerHTML = "";
  (cfg.repos || []).forEach((r, i) => reposEl.appendChild(renderRepo(r, i)));
  if (!(cfg.repos || []).length) reposEl.appendChild(renderRepo({ url: "", branch: "main", env: {} }, 0));
}

function collectConfig() {
  const f = $("#config-form");
  const repos = [];
  $("#repos").querySelectorAll(".repo-card").forEach((card) => {
    const repo = {
      url: card.querySelector('[data-field="url"]').value.trim(),
      branch: card.querySelector('[data-field="branch"]').value.trim(),
      env: readEnvTable(card.querySelector("[data-repo-env]")),
    };
    const name = card.querySelector('[data-field="name"]').value.trim();
    const ssh = card.querySelector('[data-field="ssh_identity_file"]').value.trim();
    if (name) repo.name = name;
    if (ssh) repo.ssh_identity_file = ssh;
    repos.push(repo);
  });

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
  } else {
    hide($("#validation-errors"));
    show($("#status"), `Loaded config v${data.config.config_version || "?"}`, true);
  }
  if (data.warnings?.length) {
    show($("#status"), `Loaded (migration: ${data.warnings.join(", ")})`, true);
  }
  await refreshHistorySelectors();
}

async function saveConfig(ev) {
  ev.preventDefault();
  const cfg = collectConfig();
  const res = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
  const data = await res.json();
  if (!data.ok) {
    show($("#status"), "Save failed", false);
    showValidationErrors(data.errors);
    return;
  }
  hide($("#validation-errors"));
  show($("#status"), `Saved config v${data.config_version}`, true);
  await loadConfig();
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

document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $("#panel-editor").classList.toggle("hidden", tab !== "editor");
    $("#panel-history").classList.toggle("hidden", tab !== "history");
    if (tab === "history") refreshHistorySelectors();
  };
});

$("#add-global-env").onclick = () => addEnvRow($("#start-sh-env"), "", "");
$("#add-repo").onclick = () => {
  const n = $("#repos").querySelectorAll(".repo-card").length;
  $("#repos").appendChild(renderRepo({ url: "", branch: "main", env: {} }, n));
};
$("#config-form").onsubmit = saveConfig;
$("#reload-btn").onclick = () => loadConfig();
$("#run-diff").onclick = runDiff;

loadConfig();
