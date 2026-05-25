const state = {
  turnId: null,
  activeTab: "archive",
  profile: null,
  memory: null,
  reminders: null,
  skills: null,
  persona: null,
  configNoticeShown: false,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data;
}

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.textContent = text;
  $("messages").appendChild(el);
  $("messages").scrollTop = $("messages").scrollHeight;
}

function renderTrace(toolCalls) {
  const trace = $("toolTrace");
  trace.innerHTML = "";
  if (!toolCalls || toolCalls.length === 0) {
    trace.appendChild(li("未调用工具"));
    return;
  }
  toolCalls.forEach((call) => {
    const result = summarizeToolResult(call);
    const source = call.source ? ` · ${call.source}` : "";
    trace.appendChild(li(`${call.name}${source} ${JSON.stringify(call.arguments || {})}${result}`));
  });
}

function summarizeToolResult(call) {
  if (!call.result) return "";
  try {
    const data = JSON.parse(call.result);
    if (call.name === "weather") {
      return ` -> ${data.city || ""} ${data.condition || ""} ${data.temperature_c ?? "-"}°C`;
    }
    return " -> done";
  } catch {
    return " -> done";
  }
}

function renderEmotion(emotion) {
  const box = $("emotionBox");
  const label = emotion?.label || "neutral";
  const strength = Number(emotion?.strength || 0).toFixed(1);
  box.className = `emotion ${label}`;
  box.innerHTML = `<strong>${label}</strong><span>${strength}</span>`;
}

function renderSupport(support) {
  const box = $("supportBox");
  if (!support) {
    box.innerHTML = "<strong>无策略</strong><p>本轮未生成情绪支持策略。</p>";
    return;
  }
  const safety = support.safety_note
    ? `<p class="safety">${escapeHtml(support.safety_note)}</p>`
    : "";
  box.innerHTML = `
    <strong>${escapeHtml(support.mode)} · ${escapeHtml(support.need)}</strong>
    <p>${escapeHtml(support.mirror)}</p>
    <p>${escapeHtml(support.small_action)}</p>
    ${safety}
  `;
}

function li(text) {
  const item = document.createElement("li");
  item.textContent = text;
  return item;
}

async function loadStatus() {
  const data = await api("/api/status");
  $("statusDot").classList.toggle("ok", data.configured);
  $("modelText").textContent = data.model || "-";
  $("personaText").textContent = data.persona?.name || "-";
  $("toolCount").textContent = `${(data.tools || []).length} 个`;
  $("jobCount").textContent = `${(data.scheduler_jobs || []).length} 个`;
  state.persona = data.persona || state.persona;
  if (!data.configured && !state.configNoticeShown) {
    state.configNoticeShown = true;
    addMessage("system", "后端已启动，但 config.yaml 未配置 LLM api_key。状态面板可用，对话不可用。");
  }
}

async function refreshInspectors() {
  const [profile, memory, reminders, skills, persona] = await Promise.all([
    api("/api/profile"),
    api("/api/memory"),
    api("/api/reminders"),
    api("/api/skills"),
    api("/api/persona"),
  ]);
  state.profile = profile;
  state.memory = memory;
  state.reminders = reminders;
  state.skills = skills;
  state.persona = persona.persona || persona;
  renderTab();
}

function renderTab() {
  const root = $("tabContent");
  root.innerHTML = "";
  if (state.activeTab === "archive") renderArchive(root);
  if (state.activeTab === "profile") renderProfile(root);
  if (state.activeTab === "reminders") renderReminders(root);
  if (state.activeTab === "persona") renderPersona(root);
  if (state.activeTab === "skills") renderSkills(root);
}

function renderArchive(root) {
  const items = state.memory?.archive || [];
  if (!items.length) return empty(root, "暂无档案记忆");
  items.slice(0, 12).forEach((m) => {
    const meta = m.metadata || {};
    root.appendChild(card(meta.type || "memory", m.content, []));
  });
}

function renderProfile(root) {
  const fields = state.profile?.fields || {};
  Object.entries(fields).forEach(([key, value]) => {
    root.appendChild(card(key, value, []));
  });
  const claims = state.profile?.claims || [];
  claims.slice(0, 8).forEach((c) => {
    root.appendChild(card(`claim ${(c.confidence * 100).toFixed(0)}%`, c.claim, []));
  });
  if (!Object.keys(fields).length && !claims.length) empty(root, "暂无用户画像");
}

function renderReminders(root) {
  const reminders = state.reminders?.reminders || [];
  const pending = state.reminders?.pending_messages || [];
  reminders.slice(0, 10).forEach((r) => {
    root.appendChild(card(r.status, `${r.content}\n${r.trigger_at}`, []));
  });
  pending.slice(0, 6).forEach((p) => {
    root.appendChild(card(p.source, `${p.content}\n${p.scheduled_at}`, []));
  });
  if (!reminders.length && !pending.length) empty(root, "暂无提醒");
}

function renderSkills(root) {
  const skills = state.skills?.skills || [];
  if (!skills.length) return empty(root, "暂无 skill");
  skills.slice(0, 12).forEach((s) => {
    root.appendChild(
      card(
        `${s.name} ${(s.confidence * 100).toFixed(0)}%`,
        `success ${s.success_count} / fail ${s.fail_count}`,
        s.triggers || [],
      ),
    );
  });
}

function renderPersona(root) {
  const p = state.persona || {};
  const habits = (p.response_habits || []).join("\n");
  root.innerHTML = `
    <form id="personaForm" class="persona-form">
      <label>
        名字
        <input id="personaName" type="text" value="${escapeHtml(p.name || "")}" />
      </label>
      <label>
        关系定位
        <textarea id="personaRelationship" rows="3">${escapeHtml(p.relationship || "")}</textarea>
      </label>
      <label>
        整体风格
        <textarea id="personaStyle" rows="2">${escapeHtml(p.style || "")}</textarea>
      </label>
      <label>
        语气细节
        <textarea id="personaTone" rows="3">${escapeHtml(p.tone || "")}</textarea>
      </label>
      <label>
        回应习惯
        <textarea id="personaHabits" rows="5">${escapeHtml(habits)}</textarea>
      </label>
      <label>
        边界
        <textarea id="personaBoundaries" rows="3">${escapeHtml(p.boundaries || "")}</textarea>
      </label>
      <label>
        回复语言
        <input id="personaLanguage" type="text" value="${escapeHtml(p.language || "中文")}" />
      </label>
      <label>
        称呼用户
        <input id="personaAddress" type="text" value="${escapeHtml(p.address_user || "你")}" />
      </label>
      <button type="submit">保存人格</button>
    </form>
  `;
  $("personaForm").addEventListener("submit", savePersona);
}

async function savePersona(event) {
  event.preventDefault();
  const payload = {
    name: $("personaName").value.trim(),
    relationship: $("personaRelationship").value.trim(),
    style: $("personaStyle").value.trim(),
    tone: $("personaTone").value.trim(),
    response_habits: $("personaHabits")
      .value.split("\n")
      .map((line) => line.trim())
      .filter(Boolean),
    boundaries: $("personaBoundaries").value.trim(),
    language: $("personaLanguage").value.trim(),
    address_user: $("personaAddress").value.trim(),
  };
  try {
    const data = await api("/api/persona", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    state.persona = data.persona || data;
    $("personaText").textContent = state.persona.name || "-";
    addMessage("system", "人格已保存，下一轮对话会使用新的设定。");
    renderTab();
  } catch (err) {
    addMessage("system", err.message);
  }
}

function card(title, body, tags) {
  const el = document.createElement("article");
  el.className = "item";
  const tagHtml = tags.map((t) => `<span class="tag">${escapeHtml(String(t))}</span>`).join("");
  el.innerHTML = `<h3>${escapeHtml(title)}</h3><p>${escapeHtml(body)}</p><div class="tag-row">${tagHtml}</div>`;
  return el;
}

function empty(root, text) {
  root.appendChild(card("空", text, []));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c];
  });
}

async function sendMessage(event) {
  event.preventDefault();
  const input = $("messageInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  $("sendBtn").disabled = true;
  addMessage("user", message);
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    state.turnId = data.turn_id;
    addMessage("assistant", data.text || "(无文本响应)");
    $("turnText").textContent = data.turn_id;
    $("goodBtn").disabled = false;
    $("badBtn").disabled = false;
    renderTrace(data.tool_calls);
    renderEmotion(data.emotion);
    renderSupport(data.emotional_support);
    if (data.pending_messages?.length) {
      data.pending_messages.forEach((p) => addMessage("system", `${p.source}: ${p.content}`));
    }
    await refreshInspectors();
  } catch (err) {
    addMessage("system", err.message);
  } finally {
    $("sendBtn").disabled = false;
    input.focus();
  }
}

async function sendFeedback(label) {
  if (!state.turnId) return;
  try {
    await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ label, turn_id: state.turnId }),
    });
    addMessage("system", `feedback: ${label}`);
    await refreshInspectors();
  } catch (err) {
    addMessage("system", err.message);
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    state.activeTab = tab.dataset.tab;
    renderTab();
  });
});

$("chatForm").addEventListener("submit", sendMessage);
$("goodBtn").addEventListener("click", () => sendFeedback("good"));
$("badBtn").addEventListener("click", () => sendFeedback("bad"));

loadStatus()
  .then(refreshInspectors)
  .catch((err) => addMessage("system", err.message));
