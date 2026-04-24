/**
 * Web-Automi Dashboard — WebSocket client + UI logic
 */

// ── DOM refs ──────────────────────────────────────────
const sidebar       = document.getElementById("sidebar");
const sidebarArrow  = document.getElementById("sidebar-arrow");
const btnSidebarOpen= document.getElementById("btn-sidebar-open");
const btnSidebarClose= document.getElementById("btn-sidebar-close");
const sidebarBackdrop= document.getElementById("sidebar-backdrop");

const mainWrapper   = document.getElementById("main-wrapper");
const taskInput     = document.getElementById("task-input");

// Desktop controls
const btnRunDesktop = document.getElementById("btn-run-desktop");
const btnStopDesktop = document.getElementById("btn-stop-desktop");
const statusChipDesktop = document.getElementById("status-chip-desktop");

// Mobile controls
const btnRunMobile  = document.getElementById("btn-run-mobile");
const btnStopMobile = document.getElementById("btn-stop-mobile");
const statusChipMobile = document.getElementById("status-chip-mobile");

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

const userName      = document.getElementById("user-name");
const userAvatar    = document.getElementById("user-avatar");
const btnLogout     = document.getElementById("btn-logout");

let isRunning = false;
let ws = null;
let stepCount = 0;

// ══════════════════════════════════════════════════════
// SIDEBAR
// ══════════════════════════════════════════════════════
// Desktop Sidebar Toggle
document.getElementById("btn-sidebar-toggle").addEventListener("click", () => {
  sidebar.classList.toggle("collapsed");
  sidebarArrow.textContent = sidebar.classList.contains("collapsed") ? "chevron_right" : "chevron_left";
});

// Mobile Sidebar Toggle
function closeMobileSidebar() {
  sidebar.classList.add("-translate-x-full");
  sidebarBackdrop.classList.add("hidden", "opacity-0");
  sidebarBackdrop.classList.remove("pointer-events-auto");
}
function openMobileSidebar() {
  sidebar.classList.remove("-translate-x-full");
  sidebarBackdrop.classList.remove("hidden");
  // tiny delay to allow display:block to apply before animating opacity
  setTimeout(() => {
    sidebarBackdrop.classList.remove("opacity-0");
    sidebarBackdrop.classList.add("pointer-events-auto");
  }, 10);
}

btnSidebarOpen.addEventListener("click", openMobileSidebar);
btnSidebarClose.addEventListener("click", closeMobileSidebar);
sidebarBackdrop.addEventListener("click", closeMobileSidebar);

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

    if (window.innerWidth < 768) {
      closeMobileSidebar();
    }
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
  
  document.querySelectorAll(".status-label-ui").forEach(el => el.textContent = label || state || "Idle");
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
  
  // Icon resolution based on status/type
  let iconHTML = '';
  if (step.status === 'success' || step.type === 'done') {
      iconHTML = `<span class="material-symbols-outlined text-[12px] text-green-400 font-bold bg-green-500/20 rounded-full w-4 h-4 flex items-center justify-center absolute left-[-6px] top-[4px] z-10 shadow-[0_0_0_4px_rgba(74,222,128,0.2)]">check</span>`;
  } else if (step.status === 'failed' || step.type === 'error') {
      iconHTML = `<span class="material-symbols-outlined text-[12px] text-red-400 font-bold bg-red-500/20 rounded-full w-4 h-4 flex items-center justify-center absolute left-[-6px] top-[4px] z-10 shadow-[0_0_0_4px_rgba(248,113,113,0.2)]">close</span>`;
  } else if (step.status === 'running') {
      iconHTML = `<span class="material-symbols-outlined text-[12px] text-yellow-400 font-bold bg-yellow-500/20 rounded-full w-4 h-4 flex items-center justify-center absolute left-[-6px] top-[4px] z-10 shadow-[0_0_0_4px_rgba(250,204,21,0.2)] animate-spin" style="font-variation-settings: 'FILL' 1, 'wght' 600;">sync</span>`;
  } else {
      iconHTML = `<div class="step-dot ${step.status}"></div>`;
  }

  const titleStr = step.title ? step.title.replace(/[\u1000-\uFFFF]+/g, '').trim() : '';
  const detailStr = step.detail ? step.detail.replace(/[\u1000-\uFFFF]+/g, '').trim() : '';

  const div = document.createElement("div");
  div.className = "relative pl-6";
  div.innerHTML = `
    ${iconHTML}
    ${!isLast ? '<div class="step-connector"></div>' : ''}
    <p class="text-[11px] text-zinc-400 font-medium mb-0.5">${formatTime(step.timestamp)}</p>
    <p class="text-sm text-zinc-100 font-medium">${esc(titleStr)}</p>
    ${detailStr ? `<div class="mt-1 bg-white/5 rounded p-1.5 text-[12px] text-zinc-400 font-mono border border-white/10 break-words">${esc(detailStr)}</div>` : ''}
  `;
  stepList.appendChild(div);
  stepList.scrollTop = stepList.scrollHeight;
}

// ══════════════════════════════════════════════════════
// LOG RENDERING
// ══════════════════════════════════════════════════════
function addLog(step) {
  const ph = logBody.querySelector("p.text-zinc-400, p.text-slate-400");
  if (ph) ph.remove();

  let cls = "log-info";
  let tag = "info";
  if (step.status === "success" || step.type === "done") { cls = "log-success"; tag = "success"; }
  else if (step.status === "failed" || step.type === "error") { cls = "log-error"; tag = "error"; }
  else if (step.type === "browsing") { cls = "log-warn"; tag = "browser"; }

  const titleStr = step.title ? step.title.replace(/[\u1000-\uFFFF]+/g, '').trim() : '';
  const detailStr = step.detail ? step.detail.replace(/[\u1000-\uFFFF]+/g, '').trim() : '';

  const p = document.createElement("p");
  p.innerHTML = `<span class="${cls}">[${tag}]</span> ${esc(titleStr)}${detailStr ? ': ' + esc(detailStr.slice(0, 120)) : ''}`;
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
let taskStartTime = 0;

function runTask(prompt) {
  if (isRunning || !prompt.trim()) return;
  isRunning = true;
  taskStartTime = Date.now();
  hideError();
  resetAgent();
  showView("agent");

  // Activate nav
  document.querySelectorAll(".nav-link").forEach(l => l.classList.remove("active"));
  document.querySelector('[data-view="agent"]').classList.add("active");

  setStatus("running", "Running");
  
  document.querySelectorAll(".btn-run-ui").forEach(b => b.classList.add("hidden"));
  document.querySelectorAll(".btn-stop-ui").forEach(b => b.classList.remove("hidden"));
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
        if (msg.data.type === "stream") {
            if (resultCard.classList.contains("hidden")) {
                setStatus("running", "Writing Response");
                resultCard.classList.remove("hidden");
                resultBody.innerHTML = "";
                window.streamedResult = "";
            }
            window.streamedResult += (msg.data.detail || "");
            resultBody.innerHTML = renderMarkdown(window.streamedResult);
        } else {
            addStep(msg.data);
            addLog(msg.data);
        }
        break;

      case "result":
        if (msg.data.error) {
          setStatus("failed", "Failed");
          showError(msg.data.result || "Task failed");
          resultCard.classList.remove("hidden");
          resultBody.innerHTML = `<p style="color:#fca5a5;">${esc(msg.data.result || "An error occurred.")}</p>`;
        } else {
          const elapsed = ((Date.now() - taskStartTime) / 1000).toFixed(1);
          setStatus("success", `Completed in ${elapsed}s`);
          resultCard.classList.remove("hidden");
          if (!window.streamedResult) {
              resultBody.innerHTML = renderMarkdown(msg.data.result || "No result.");
          }
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
  document.querySelectorAll(".btn-run-ui").forEach(b => b.classList.remove("hidden"));
  document.querySelectorAll(".btn-stop-ui").forEach(b => b.classList.add("hidden"));
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
          <p class="text-sm font-medium text-zinc-100 truncate">${esc(t.prompt)}</p>
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
btnRunDesktop.addEventListener("click", () => runTask(taskInput.value));
btnStopDesktop.addEventListener("click", stopTask);
btnRunMobile.addEventListener("click", () => runTask(taskInput.value));
btnStopMobile.addEventListener("click", stopTask);
taskInput.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); runTask(taskInput.value); } });
document.querySelectorAll(".example-chip").forEach(c => c.addEventListener("click", () => { taskInput.value = c.dataset.prompt; runTask(c.dataset.prompt); }));

btnLogout.addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/auth";
});

// Auth init
async function initAuth() {
  try {
    const res = await fetch("/api/me");
    if (!res.ok) {
      window.location.href = "/auth";
      return;
    }
    const data = await res.json();
    userName.textContent = data.user.username;
    userAvatar.innerHTML = `<span class="text-xs font-bold text-zinc-400">${data.user.username.charAt(0).toUpperCase()}</span>`;
    taskInput.focus();
  } catch (err) {
    window.location.href = "/auth";
  }
}

document.addEventListener("DOMContentLoaded", initAuth);

// Copy logs
const btnCopyLogs = document.getElementById("btn-copy-logs");
if (btnCopyLogs) {
  btnCopyLogs.addEventListener("click", () => {
    const logText = Array.from(logBody.querySelectorAll('p')).map(p => p.innerText).join('\n');
    navigator.clipboard.writeText(logText).then(() => {
      const oldHtml = btnCopyLogs.innerHTML;
      btnCopyLogs.innerHTML = `<span class="material-symbols-outlined text-[14px]">check</span> Copied`;
      setTimeout(() => btnCopyLogs.innerHTML = oldHtml, 2000);
    });
  });
}
