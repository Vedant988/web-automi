/**
 * Web-Automi Dashboard — WebSocket client + UI logic
 */

// ── DOM refs ──────────────────────────────────────────
const sidebar       = document.getElementById("sidebar");
const sidebarArrow  = document.getElementById("sidebar-arrow");
const mainWrapper   = document.getElementById("main-wrapper");
const taskInput     = document.getElementById("task-input");
const btnRun        = document.getElementById("btn-run");
const btnStop       = document.getElementById("btn-stop");
const statusChip    = document.getElementById("status-chip");
const statusLabel   = document.getElementById("status-label");
const stepBadge     = document.getElementById("step-badge");
const stepNum       = document.getElementById("step-num");
const errorBanner   = document.getElementById("error-banner");
const errorText     = document.getElementById("error-text");
const welcomePane   = document.getElementById("welcome-pane");
const agentView     = document.getElementById("agent-view");
const historyView   = document.getElementById("history-view");
const stepList      = document.getElementById("step-list");
const stepCountBadge= document.getElementById("step-count-badge");
const logBody       = document.getElementById("log-body");
const resultCard    = document.getElementById("result-card");
const resultBody    = document.getElementById("result-body");
const historyList   = document.getElementById("history-list");
const settingModel  = document.getElementById("setting-model");

let isRunning = false;
let ws = null;
let stepCount = 0;

// ══════════════════════════════════════════════════════
// SIDEBAR
// ══════════════════════════════════════════════════════
document.getElementById("btn-sidebar-toggle").addEventListener("click", () => {
  sidebar.classList.toggle("collapsed");
  sidebarArrow.textContent = sidebar.classList.contains("collapsed") ? "chevron_right" : "chevron_left";
});

// Nav links
document.querySelectorAll(".nav-link").forEach(link => {
  link.addEventListener("click", e => {
    e.preventDefault();
    document.querySelectorAll(".nav-link").forEach(l => l.classList.remove("active"));
    link.classList.add("active");
    const view = link.dataset.view;
    if (view === "history") { showView("history"); loadHistory(); }
    else if (view === "agent") showView("agent");
    else if (view === "dashboard") showView("welcome");
    else if (view === "settings") showView("welcome");
  });
});

function showView(name) {
  welcomePane.classList.toggle("hidden", name !== "welcome");
  agentView.classList.toggle("hidden", name !== "agent");
  historyView.classList.toggle("hidden", name !== "history");
}

// ══════════════════════════════════════════════════════
// STATUS
// ══════════════════════════════════════════════════════
function setStatus(state, label) {
  document.body.className = document.body.className.replace(/status-\w+/g, "");
  if (state) document.body.classList.add("status-" + state);
  statusLabel.textContent = label || state || "Idle";
}

function showError(msg) {
  errorText.textContent = msg;
  errorBanner.classList.remove("hidden");
}
function hideError() { errorBanner.classList.add("hidden"); }

document.getElementById("btn-dismiss-error").addEventListener("click", hideError);

// ══════════════════════════════════════════════════════
// STEP RENDERING (timeline)
// ══════════════════════════════════════════════════════
function resetAgent() {
  stepCount = 0;
  stepList.innerHTML = "";
  logBody.innerHTML = "";
  resultCard.classList.add("hidden");
  stepCountBadge.textContent = "0 Steps";
  stepBadge.classList.add("hidden");
  stepNum.textContent = "0";
}

function addStep(step) {
  stepCount = step.step_number;
  stepCountBadge.textContent = stepCount + " Steps";
  stepBadge.classList.remove("hidden");
  stepNum.textContent = stepCount;

  // Remove placeholder
  const ph = stepList.querySelector("p");
  if (ph) ph.remove();

  const isLast = step.type === "done" || step.type === "error";
  const div = document.createElement("div");
  div.className = "relative pl-6";
  div.innerHTML = `
    <div class="step-dot ${step.status}"></div>
    ${!isLast ? '<div class="step-connector"></div>' : ''}
    <p class="text-[11px] text-slate-400 font-medium mb-0.5">${formatTime(step.timestamp)}</p>
    <p class="text-sm text-slate-700 font-medium">${esc(step.title)}</p>
    ${step.detail ? `<div class="mt-1 bg-slate-50 rounded p-1.5 text-[11px] text-slate-500 font-mono border border-slate-100 break-all">${esc(step.detail)}</div>` : ''}
  `;
  stepList.appendChild(div);
  stepList.scrollTop = stepList.scrollHeight;
}

// ══════════════════════════════════════════════════════
// LOG RENDERING
// ══════════════════════════════════════════════════════
function addLog(step) {
  const ph = logBody.querySelector("p.text-slate-400");
  if (ph) ph.remove();

  let cls = "log-info";
  let tag = "info";
  if (step.status === "success" || step.type === "done") { cls = "log-success"; tag = "success"; }
  else if (step.status === "failed" || step.type === "error") { cls = "log-error"; tag = "error"; }
  else if (step.type === "browsing") { cls = "log-warn"; tag = "browser"; }

  const p = document.createElement("p");
  p.innerHTML = `<span class="${cls}">[${tag}]</span> ${esc(step.title)}${step.detail ? ': ' + esc(step.detail.slice(0, 120)) : ''}`;
  logBody.appendChild(p);
  logBody.scrollTop = logBody.scrollHeight;
}

// ══════════════════════════════════════════════════════
// MARKDOWN RENDERER (with tables)
// ══════════════════════════════════════════════════════
function renderMarkdown(text) {
  if (!text) return "";
  let t = text;

  // Code blocks
  t = t.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${esc(code.trim())}</code></pre>`);

  // Tables: detect lines starting with |
  t = t.replace(/^(\|.+\|)\n(\|[\-:\| ]+\|)\n((?:\|.+\|\n?)*)/gm, (_, headerLine, sepLine, bodyBlock) => {
    const headers = headerLine.split("|").slice(1, -1).map(h => h.trim());
    const rows = bodyBlock.trim().split("\n").filter(r => r.includes("|")).map(r => r.split("|").slice(1, -1).map(c => c.trim()));
    let html = '<table><thead><tr>';
    headers.forEach(h => html += `<th>${renderInline(h)}</th>`);
    html += '</tr></thead><tbody>';
    rows.forEach(row => {
      html += '<tr>';
      row.forEach(cell => html += `<td>${renderInline(cell)}</td>`);
      html += '</tr>';
    });
    html += '</tbody></table>';
    return html;
  });

  // Now split by remaining lines and process inline
  const lines = t.split("\n");
  let result = [];
  let inList = false;

  for (const line of lines) {
    // Skip if already HTML (table/pre)
    if (line.startsWith("<table") || line.startsWith("<pre") || line.startsWith("</")) {
      if (inList) { result.push("</ul>"); inList = false; }
      result.push(line);
      continue;
    }

    const trimmed = line.trim();
    if (!trimmed) {
      if (inList) { result.push("</ul>"); inList = false; }
      continue;
    }

    // Bullet list
    if (/^[-•]\s+/.test(trimmed)) {
      if (!inList) { result.push("<ul>"); inList = true; }
      result.push(`<li>${renderInline(trimmed.replace(/^[-•]\s+/, ""))}</li>`);
      continue;
    }

    // Numbered list
    if (/^\d+\.\s+/.test(trimmed)) {
      if (!inList) { result.push("<ul>"); inList = true; }
      result.push(`<li>${renderInline(trimmed.replace(/^\d+\.\s+/, ""))}</li>`);
      continue;
    }

    if (inList) { result.push("</ul>"); inList = false; }
    result.push(`<p>${renderInline(trimmed)}</p>`);
  }
  if (inList) result.push("</ul>");

  return result.join("\n");
}

function renderInline(text) {
  let t = esc(text);
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(https?:\/\/[^\s<)]+)/g, '<a href="$1" target="_blank">$1</a>');
  return t;
}

// ══════════════════════════════════════════════════════
// WEBSOCKET — RUN TASK
// ══════════════════════════════════════════════════════
function runTask(prompt) {
  if (isRunning || !prompt.trim()) return;
  isRunning = true;
  hideError();
  resetAgent();
  showView("agent");

  // Activate nav
  document.querySelectorAll(".nav-link").forEach(l => l.classList.remove("active"));
  document.querySelector('[data-view="agent"]').classList.add("active");

  setStatus("running", "Running");
  btnRun.classList.add("hidden");
  btnStop.classList.remove("hidden");
  taskInput.disabled = true;

  const model = settingModel.value;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${location.host}/ws/run`);

  ws.onopen = () => {
    ws.send(JSON.stringify({ prompt: prompt.trim(), model }));
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    switch (msg.type) {
      case "started":
        addLog({ title: "Task started", detail: prompt.trim(), status: "success", type: "init" });
        break;

      case "step":
        addStep(msg.data);
        addLog(msg.data);
        break;

      case "result":
        if (msg.data.error) {
          setStatus("failed", "Failed");
          showError(msg.data.result || "Task failed");
          resultCard.classList.remove("hidden");
          resultBody.innerHTML = `<p style="color:#fca5a5;">${esc(msg.data.result || "An error occurred.")}</p>`;
        } else {
          setStatus("success", "Completed");
          resultCard.classList.remove("hidden");
          resultBody.innerHTML = renderMarkdown(msg.data.result || "No result.");
        }
        break;

      case "error":
        setStatus("failed", "Error");
        showError(msg.data.error || "Unknown error");
        break;

      case "done":
        finishRun();
        break;
    }
  };

  ws.onerror = () => {
    showError("WebSocket connection failed.");
    finishRun();
    setStatus("failed", "Error");
  };

  ws.onclose = () => {
    finishRun();
  };
}

function finishRun() {
  isRunning = false;
  btnRun.classList.remove("hidden");
  btnStop.classList.add("hidden");
  taskInput.disabled = false;
  taskInput.focus();
  ws = null;
}

function stopTask() {
  fetch("/api/stop", { method: "POST" }).catch(() => {});
  if (ws) ws.close();
  setStatus("failed", "Stopped");
  finishRun();
}

// ══════════════════════════════════════════════════════
// HISTORY
// ══════════════════════════════════════════════════════
async function loadHistory() {
  try {
    const tasks = await fetch("/api/tasks").then(r => r.json());
    if (!tasks.length) {
      historyList.innerHTML = '<p class="text-sm text-slate-400 text-center py-12">No tasks yet.</p>';
      return;
    }
    historyList.innerHTML = tasks.map(t => `
      <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-[0_2px_12px_rgba(0,0,0,0.04)] hover:border-slate-300 transition cursor-pointer flex items-start justify-between gap-4" onclick="viewTask('${t.id}')">
        <div class="flex-1 min-w-0">
          <p class="text-sm font-medium text-slate-800 truncate">${esc(t.prompt)}</p>
          <div class="flex items-center gap-3 mt-2">
            <span class="text-[11px] font-semibold uppercase px-2 py-0.5 rounded ${t.status === 'completed' ? 'bg-green-50 text-green-700 border border-green-200' : t.status === 'failed' ? 'bg-red-50 text-red-700 border border-red-200' : 'bg-yellow-50 text-yellow-700 border border-yellow-200'}">${t.status}</span>
            <span class="text-[11px] text-slate-400">${timeAgo(t.created_at)}</span>
          </div>
        </div>
        <button class="text-slate-300 hover:text-red-500 transition p-1" onclick="event.stopPropagation(); deleteTask('${t.id}')">
          <span class="material-symbols-outlined text-[18px]">delete</span>
        </button>
      </div>
    `).join("");
  } catch {
    historyList.innerHTML = '<p class="text-sm text-slate-400 text-center py-12">Failed to load.</p>';
  }
}

async function viewTask(id) {
  try {
    const task = await fetch(`/api/tasks/${id}`).then(r => r.json());
    showView("agent");
    resetAgent();

    document.querySelectorAll(".nav-link").forEach(l => l.classList.remove("active"));
    document.querySelector('[data-view="agent"]').classList.add("active");

    if (task.steps) {
      task.steps.forEach(s => { addStep({ ...s, step_number: s.step_number || 0 }); addLog(s); });
    }
    if (task.result) {
      resultCard.classList.remove("hidden");
      resultBody.innerHTML = renderMarkdown(task.result);
      setStatus(task.status === "completed" ? "success" : "failed", task.status === "completed" ? "Completed" : "Failed");
    }
    taskInput.value = task.prompt;
  } catch {}
}

async function deleteTask(id) {
  await fetch(`/api/tasks/${id}`, { method: "DELETE" }).catch(() => {});
  loadHistory();
}

// ══════════════════════════════════════════════════════
// COPY
// ══════════════════════════════════════════════════════
document.getElementById("btn-copy").addEventListener("click", () => {
  const text = resultBody.innerText;
  navigator.clipboard.writeText(text).then(() => {
    document.getElementById("btn-copy").innerHTML = '<span class="material-symbols-outlined text-[16px]">check</span> Copied!';
    setTimeout(() => {
      document.getElementById("btn-copy").innerHTML = '<span class="material-symbols-outlined text-[16px]">content_copy</span> Copy Results';
    }, 2000);
  });
});

// ══════════════════════════════════════════════════════
// UTILITIES
// ══════════════════════════════════════════════════════
function esc(t) {
  const d = document.createElement("div"); d.textContent = t || ""; return d.innerHTML;
}

function formatTime(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
  catch { return ""; }
}

function timeAgo(iso) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  return new Date(iso).toLocaleDateString();
}

// ══════════════════════════════════════════════════════
// EVENT LISTENERS
// ══════════════════════════════════════════════════════
btnRun.addEventListener("click", () => runTask(taskInput.value));
btnStop.addEventListener("click", stopTask);
taskInput.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); runTask(taskInput.value); } });
document.querySelectorAll(".example-chip").forEach(c => c.addEventListener("click", () => { taskInput.value = c.dataset.prompt; runTask(c.dataset.prompt); }));

document.addEventListener("DOMContentLoaded", () => taskInput.focus());
