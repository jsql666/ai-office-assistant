const assistantList = document.getElementById("assistant-list");
const sessionList = document.getElementById("session-list");
const newSessionButton = document.getElementById("new-session");
const assistantName = document.getElementById("assistant-name");
const assistantDescription = document.getElementById("assistant-description");
const messagesPanel = document.getElementById("messages");
const conversation = document.querySelector(".conversation");
const emptyState = document.getElementById("empty-state");
const emptyTitle = document.getElementById("empty-title");
const useExample = document.getElementById("use-example");
const composer = document.getElementById("composer");
const messageInput = document.getElementById("message");
const sendButton = document.getElementById("send");
const statusPanel = document.getElementById("status");
const editRole = document.getElementById("edit-role");
const clearSessionButton = document.getElementById("clear-session");
const exportSessionButton = document.getElementById("export-session");
const deleteSessionButton = document.getElementById("delete-session");
const roleDialog = document.getElementById("role-dialog");
const rolePrompt = document.getElementById("role-prompt");
const roleForm = document.getElementById("role-form");
const sessionActions = [editRole, clearSessionButton, exportSessionButton, deleteSessionButton];
const assistantKey = "office-assistant.current";
const sessionKey = "office-assistant.session.";

let assistants = [];
let activeAssistant = null;
let activeSession = "";
let busy = false;

function setStatus(text, error = false) {
    statusPanel.textContent = text;
    statusPanel.dataset.error = String(error);
}

function updateControls() {
    for (const button of document.querySelectorAll(".sidebar-choice")) {
        button.disabled = busy;
    }
    newSessionButton.disabled = busy || !activeAssistant;
    const unavailable = busy || !activeSession;
    for (const button of sessionActions) {
        button.disabled = unavailable;
    }
    messageInput.disabled = sendButton.disabled = useExample.disabled = unavailable;
    messageInput.placeholder = activeSession ? "输入消息，按 Enter 发送" : "新建会话后输入消息";
}

async function run(task) {
    if (busy) return;
    busy = true;
    updateControls();
    setStatus("");
    try {
        await task();
    } catch (error) {
        setStatus(error.message || "操作失败，请重试。", true);
    } finally {
        busy = false;
        updateControls();
    }
}

async function request(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        const message = typeof body?.detail === "string" ? body.detail : "请求失败，请重试。";
        const error = new Error(message);
        error.status = response.status;
        throw error;
    }
    return response;
}

function post(path, data) {
    return request(path, {method: "POST", body: new URLSearchParams(data)});
}

function choice(label, selected, click) {
    const button = document.createElement("button");
    button.className = "sidebar-choice";
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-current", String(selected));
    button.disabled = busy;
    button.addEventListener("click", click);
    return button;
}

function renderAssistants() {
    assistantList.replaceChildren(...assistants.map(assistant => choice(
        assistant.name,
        assistant.id === activeAssistant?.id,
        () => run(() => switchAssistant(assistant)),
    )));
}

async function loadSessions() {
    const result = await (await request("/list_sessions?assistant_type=" + encodeURIComponent(activeAssistant.id))).json();
    const choices = result.details.map(session => {
        const title = session.title?.trim() || "新会话 · " + session.session_id.slice(0, 6);
        const button = choice(title, session.session_id === activeSession, () => run(async () => {
            await openSession(session.session_id);
            await loadSessions();
        }));
        button.title = title + " · " + session.updated_at.replace("T", " ");
        button.dataset.sessionId = session.session_id;
        return button;
    });
    sessionList.replaceChildren(...choices);
    if (!choices.length) {
        const hint = document.createElement("p");
        hint.className = "session-hint";
        hint.textContent = "暂无历史会话";
        sessionList.append(hint);
    }
    return result.sessions;
}

function showEmptyState() {
    emptyState.hidden = messagesPanel.childElementCount > 0;
    emptyTitle.textContent = activeSession ? "开始新的对话" : "新建会话开始工作";
    useExample.hidden = !activeSession || !activeAssistant;
    useExample.textContent = activeAssistant?.example || "";
}

function scrollToLatest() {
    conversation.scrollTop = conversation.scrollHeight;
}

function appendMessage(type, text) {
    const message = document.createElement("article");
    message.className = "message message--" + type;
    const label = document.createElement("p");
    label.className = "message-label";
    label.textContent = type === "user" ? "你" : activeAssistant.name;
    const body = document.createElement("p");
    body.className = "message-text";
    body.textContent = text;
    message.append(label, body);
    messagesPanel.append(message);
    return {message, body};
}

async function openSession(sessionId) {
    const history = await (await request("/history/" + encodeURIComponent(sessionId))).json();
    activeSession = sessionId;
    localStorage.setItem(sessionKey + activeAssistant.id, sessionId);
    messageInput.value = "";
    messagesPanel.replaceChildren();
    for (const item of history.history) {
        appendMessage(item.type, item.content);
    }
    showEmptyState();
    scrollToLatest();
}

async function switchAssistant(assistant) {
    activeAssistant = assistant;
    activeSession = "";
    assistantName.textContent = assistant.name;
    assistantDescription.textContent = assistant.description;
    localStorage.setItem(assistantKey, assistant.id);
    messageInput.value = "";
    messagesPanel.replaceChildren();
    renderAssistants();
    showEmptyState();

    // 先加载当前分类，再从该分类的历史里恢复上一次会话。
    const sessions = await loadSessions();
    const remembered = localStorage.getItem(sessionKey + assistant.id);
    if (sessions.length) {
        try {
            await openSession(sessions.includes(remembered) ? remembered : sessions[0]);
        } catch (error) {
            if (error.status !== 404) throw error;
            localStorage.removeItem(sessionKey + assistant.id);
        }
        await loadSessions();
    }
}

newSessionButton.addEventListener("click", () => run(async () => {
    const created = await (await post("/create_session", {assistant_type: activeAssistant.id})).json();
    await openSession(created.session_id);
    await loadSessions();
}));

editRole.addEventListener("click", () => run(async () => {
    const role = await (await request("/get_role/" + encodeURIComponent(activeSession))).json();
    rolePrompt.value = role.system_prompt;
    roleDialog.showModal();
}));

document.getElementById("cancel-role").addEventListener("click", () => roleDialog.close());
roleForm.addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
        const prompt = rolePrompt.value.trim();
        if (!prompt) return;
        await post("/set_role", {session_id: activeSession, role_prompt: prompt});
        roleDialog.close();
        setStatus("角色设置已保存。");
    });
});

clearSessionButton.addEventListener("click", () => run(async () => {
    if (!window.confirm("清空当前会话的消息和记忆，并恢复该助手的默认角色？")) return;
    await post("/clear_session", {session_id: activeSession});
    await openSession(activeSession);
    await loadSessions();
    setStatus("会话已清空。");
}));

deleteSessionButton.addEventListener("click", () => run(async () => {
    if (!window.confirm("删除当前会话及其所有消息？")) return;
    await post("/delete_session", {session_id: activeSession});
    localStorage.removeItem(sessionKey + activeAssistant.id);
    await switchAssistant(activeAssistant);
    setStatus("会话已删除。");
}));

exportSessionButton.addEventListener("click", () => run(async () => {
    const sessionId = activeSession;
    const data = await (await request("/export_session/" + encodeURIComponent(sessionId))).json();
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = sessionId + ".json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus("会话已导出。");
}));

useExample.addEventListener("click", () => {
    messageInput.value = activeAssistant.example;
    messageInput.focus();
});

messageInput.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        composer.requestSubmit();
    }
});

composer.addEventListener("submit", async event => {
    event.preventDefault();
    const text = messageInput.value.trim();
    if (!text || !activeSession) return;
    await run(async () => {
        const sessionId = activeSession;
        const user = appendMessage("user", text);
        const answer = appendMessage("ai", "");
        messageInput.value = "";
        showEmptyState();
        scrollToLatest();

        let streaming = false;
        try {
            const response = await post("/stream_chat", {session_id: sessionId, msg: text});
            streaming = true;
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let reply = "";
            while (true) {
                const {done, value} = await reader.read();
                if (done) break;
                reply += decoder.decode(value, {stream: true});
                answer.body.textContent = reply;
                scrollToLatest();
            }
            answer.body.textContent = reply + decoder.decode();
        } catch (error) {
            user.message.remove();
            answer.message.remove();
            messageInput.value = text;
            showEmptyState();
            if (streaming) throw new Error("回复中断，请刷新确认历史后重试。");
            throw error;
        }

        await loadSessions();
    });
    if (!busy && activeSession) messageInput.focus();
});

run(async () => {
    const result = await (await request("/assistants")).json();
    assistants = result.assistants;
    const remembered = localStorage.getItem(assistantKey);
    const initial = assistants.find(assistant => assistant.id === remembered) || assistants[0];
    await switchAssistant(initial);
});
