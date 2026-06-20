const stateLabels = {
  ok: "正常",
  warn: "提醒",
  fail: "异常",
  unknown: "未知",
};

const dashboardOrigin = "http://127.0.0.1:8765";

if (window.location.protocol === "file:") {
  window.location.replace(`${dashboardOrigin}/`);
}

const serviceActions = {
  frigate: [
    ["docker-start", "启动 Docker Compose"],
    ["docker-restart", "重启 Docker Compose"],
    ["docker-stop", "停止 Docker Compose"],
  ],
  mosquitto: [
    ["docker-start", "启动 Docker Compose"],
    ["docker-restart", "重启 Docker Compose"],
    ["docker-stop", "停止 Docker Compose"],
  ],
  go2rtc: [
    ["go2rtc-start", "启动 go2rtc"],
    ["go2rtc-restart", "重启 go2rtc"],
    ["go2rtc-stop", "停止 go2rtc"],
  ],
  detector: [
    ["detector-start", "启动 Detector"],
    ["detector-restart", "重启 Detector"],
    ["detector-stop", "停止 Detector"],
  ],
  "token-watch": [
    ["token-watch-start", "启动监控"],
    ["token-watch-run", "运行一次"],
    ["token-watch-stop", "停止监控"],
  ],
};

const els = {
  summary: document.querySelector("#summary"),
  serviceList: document.querySelector("#service-list"),
  diagnosticList: document.querySelector("#diagnostic-list"),
  lastRefresh: document.querySelector("#last-refresh"),
  actionOutput: document.querySelector("#action-output"),
  tokenOutput: document.querySelector("#token-output"),
  tokenInput: document.querySelector("#token-input"),
  tokenSendStatus: document.querySelector("#token-send-status"),
  logOutput: document.querySelector("#log-output"),
  logSelect: document.querySelector("#log-select"),
};

let busy = false;
let tokenTimer = 0;
let tokenInputSending = false;
let tokenOutputBeforeSend = "";
let lastTokenRawOutput = "";
let tokenInputKind = "account";
let lastSentTokenValue = "";

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTime(ms) {
  if (!ms) return "尚未刷新";
  return new Date(ms).toLocaleString("zh-CN", { hour12: false });
}

function dot(state) {
  return `<span class="dot ${escapeHtml(state || "unknown")}"></span>`;
}

async function getJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function appendTokenOutput(message) {
  const current = els.tokenOutput.textContent || "";
  els.tokenOutput.textContent = `${current}${current ? "\n" : ""}${message}`;
  els.tokenOutput.scrollTop = els.tokenOutput.scrollHeight;
}

function setTokenSendStatus(message, tone = "") {
  els.tokenSendStatus.textContent = message || "";
  els.tokenSendStatus.className = `muted send-status ${tone}`.trim();
}

function updateTokenInputMode(output, serverPrompt = "") {
  const lines = String(output || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const latestPrompt = lines[lines.length - 1] || "";
  let nextKind = serverPrompt || tokenInputKind;

  if (!serverPrompt) {
    if (/请输入收到的验证码|验证码|ticket/i.test(latestPrompt)) {
      nextKind = "code";
    } else if (/密码|password/i.test(latestPrompt)) {
      nextKind = "password";
    } else if (/小米账号|手机号|邮箱/i.test(latestPrompt)) {
      nextKind = /:\s*\S+\s*$/.test(latestPrompt) ? "password" : "account";
    } else if (/正在登录小米云端|提交登录请求|安全身份验证|获取最终 token/i.test(latestPrompt)) {
      nextKind = "waiting";
    }
  }

  if (tokenInputKind !== nextKind) {
    tokenInputKind = nextKind;
    if (nextKind === "password" && els.tokenInput.value === lastSentTokenValue) {
      els.tokenInput.value = "";
    }
  }

  if (nextKind === "password") {
    els.tokenInput.type = "password";
    els.tokenInput.placeholder = "请输入小米账号密码（输入内容会隐藏）";
    if (!tokenInputSending) setTokenSendStatus("账号已提交，请输入密码。", "pending");
  } else if (nextKind === "code") {
    els.tokenInput.type = "text";
    els.tokenInput.placeholder = "请输入短信或邮箱验证码";
    if (!tokenInputSending) setTokenSendStatus("请输入收到的验证码。", "pending");
  } else if (nextKind === "waiting") {
    els.tokenInput.type = "text";
    els.tokenInput.placeholder = "正在处理，请等待脚本下一步提示";
  } else {
    els.tokenInput.type = "text";
    els.tokenInput.placeholder = "请输入小米账号、手机号或邮箱";
  }
}

async function refreshStatus() {
  try {
    const data = await getJson("/api/status");
    renderStatus(data);
  } catch (error) {
    els.summary.innerHTML = `${dot("fail")}<span>状态读取失败</span>`;
    els.actionOutput.textContent = `状态读取失败：${error.message}`;
  }
}

function renderStatus(data) {
  const overall = data.overall || { state: "unknown", label: "未知", counts: {} };
  const counts = overall.counts || {};
  els.summary.innerHTML = `
    ${dot(overall.state)}
    <span>${escapeHtml(overall.label)} · 正常 ${counts.ok || 0} / 提醒 ${counts.warn || 0} / 异常 ${counts.fail || 0}</span>
  `;
  els.lastRefresh.textContent = `上次刷新：${formatTime(data.generated_at)}`;

  const serviceOrder = ["frigate", "mosquitto", "go2rtc", "detector", "token-watch"];
  const services = (data.items || [])
    .filter((item) => item.group === "services")
    .sort((a, b) => serviceOrder.indexOf(a.key) - serviceOrder.indexOf(b.key));
  const diagnostics = (data.items || []).filter((item) => item.group !== "services");
  els.serviceList.innerHTML = services.map(renderCard).join("");
  els.diagnosticList.innerHTML = diagnostics.map(renderCard).join("");
}

function renderCard(item) {
  const actions = serviceActions[item.key] || [];
  const actionHtml = actions.map(([action, title]) => (
    `<button data-action="${escapeHtml(action)}" title="${escapeHtml(title)}">${actionLabel(action)}</button>`
  )).join("");
  const link = item.url ? ` · <a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开</a>` : "";
  const hint = item.hint ? `<p>${escapeHtml(item.hint)}</p>` : "";
  return `
    <article class="card" data-key="${escapeHtml(item.key)}">
      ${dot(item.state)}
      <div>
        <h3>${escapeHtml(item.name)} <span class="state-text ${escapeHtml(item.state)}">${escapeHtml(stateLabels[item.state] || "未知")}</span></h3>
        <p>${escapeHtml(item.detail)}${link}</p>
        ${hint}
      </div>
      <div class="card-actions">${actionHtml}</div>
    </article>
  `;
}

function actionLabel(action) {
  if (action.includes("restart")) return "重启";
  if (action.includes("start")) return "启动";
  if (action.includes("run")) return "运行";
  if (action.includes("stop")) return "停止";
  return "•";
}

async function runAction(action) {
  if (busy) return;
  busy = true;
  setButtonsDisabled(true);
  els.actionOutput.textContent = `正在执行：${action}`;
  try {
    const data = await getJson(`/api/actions/${encodeURIComponent(action)}`, { method: "POST" });
    els.actionOutput.textContent = `${data.ok ? "完成" : "未完全成功"}：${action}\n\n${data.output || ""}`;
    await refreshStatus();
    await refreshLog();
  } catch (error) {
    els.actionOutput.textContent = `执行失败：${action}\n${error.message}`;
  } finally {
    busy = false;
    setButtonsDisabled(false);
  }
}

function setButtonsDisabled(disabled) {
  document.querySelectorAll("button[data-action], #refresh-status").forEach((button) => {
    button.disabled = disabled;
  });
}

async function startTokenRefresh() {
  const data = await getJson("/api/token/start", { method: "POST" });
  els.tokenOutput.textContent = data.message || "token 刷新会话已启动。";
  setTokenSendStatus("");
  updateTokenInputMode(els.tokenOutput.textContent);
  pollToken();
}

async function sendTokenInput() {
  const value = els.tokenInput.value;
  if (!value || tokenInputSending) return;
  const inputKind = tokenInputKind === "password" || els.tokenInput.type === "password" ? "password" : tokenInputKind;
  const wasPassword = inputKind === "password";
  tokenInputSending = true;
  lastSentTokenValue = value;
  tokenOutputBeforeSend = lastTokenRawOutput;
  const sendButton = document.querySelector("#token-send");
  sendButton.disabled = true;
  sendButton.textContent = "发送中...";
  setTokenSendStatus("已发送，等待脚本响应...", "pending");
  try {
    const data = await getJson("/api/token/input", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value, kind: inputKind }),
    });
    if (!data.ok) {
      setTokenSendStatus(data.message || "输入未被接收。", "error");
    } else if (wasPassword) {
      els.tokenInput.value = "";
    }
    await pollToken();
  } catch (error) {
    setTokenSendStatus(`发送输入失败：${error.message}`, "error");
  } finally {
    tokenInputSending = false;
    sendButton.disabled = false;
    sendButton.textContent = "发送输入";
    els.tokenInput.focus();
  }
}

async function stopTokenRefresh() {
  const data = await getJson("/api/token/stop", { method: "POST" });
  els.tokenOutput.textContent += `\n${data.message || "已请求停止。"}`;
  pollToken();
}

async function pollToken() {
  try {
    const data = await getJson("/api/token/status");
    const statusLine = data.running ? "运行中" : (data.exit_code === null ? "未运行" : `已结束，返回码 ${data.exit_code}`);
    lastTokenRawOutput = data.output || "";
    els.tokenOutput.textContent = `[${statusLine}]\n${data.output || ""}`.trim();
    updateTokenInputMode(data.output || "", data.prompt || "");
    if (tokenOutputBeforeSend && (data.output || "") !== tokenOutputBeforeSend) {
      if (!["password", "code"].includes(tokenInputKind)) {
        setTokenSendStatus("脚本已响应。", "ok");
      }
      tokenOutputBeforeSend = "";
    }
    els.tokenOutput.scrollTop = els.tokenOutput.scrollHeight;
    if (data.running) {
      window.clearTimeout(tokenTimer);
      tokenTimer = window.setTimeout(pollToken, 1200);
    } else {
      await refreshStatus();
      await refreshLog();
    }
  } catch (error) {
    els.tokenOutput.textContent += `\n状态读取失败：${error.message}`;
  }
}

async function refreshLog() {
  const name = els.logSelect.value;
  try {
    const data = await getJson(`/api/logs?name=${encodeURIComponent(name)}&lines=120`);
    els.logOutput.textContent = (data.lines || []).join("\n") || "日志为空或不存在。";
    els.logOutput.scrollTop = els.logOutput.scrollHeight;
  } catch (error) {
    els.logOutput.textContent = `日志读取失败：${error.message}`;
  }
}

document.addEventListener("click", (event) => {
  const actionButton = event.target.closest("button[data-action]");
  if (actionButton) {
    runAction(actionButton.dataset.action);
  }
});

document.querySelector("#refresh-status").addEventListener("click", refreshStatus);
document.querySelector("#refresh-log").addEventListener("click", refreshLog);
document.querySelector("#token-start").addEventListener("click", startTokenRefresh);
document.querySelector("#token-stop").addEventListener("click", stopTokenRefresh);
document.querySelector("#token-send").addEventListener("click", sendTokenInput);
els.tokenInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") sendTokenInput();
});

refreshStatus();
refreshLog();
pollToken();
window.setInterval(refreshStatus, 10000);
