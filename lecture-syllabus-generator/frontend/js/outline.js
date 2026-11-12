(() => {
const taskId = new URLSearchParams(window.location.search).get("task_id");
if (!/^[1-9]\d*$/.test(taskId || "")) { window.location.href = "/"; return; }
const byId = id => document.getElementById(id);
const outlineView = byId("outline-view");
const outlineEditor = byId("outline-editor");
const editBtn = byId("edit-btn");
const saveBtn = byId("save-btn");
const cancelEditBtn = byId("cancel-edit-btn");
const regenerateBtn = byId("regenerate-btn");
const exportBtn = byId("export-btn");
const notice = byId("outline-status");
const retryBtn = byId("outline-retry-btn");
byId("outline-progress-link").href = "/progress.html?task_id=" + taskId;
if (byId('evaluation-link')) byId('evaluation-link').href = '/evaluation.html?task_id=' + taskId;
let currentMarkdown = "";
let loaded = false;
let editing = false;
let busy = false;
let regenerationUnconfirmed = false;

function updateControls() {
    editBtn.disabled = regenerateBtn.disabled = !loaded || busy;
    exportBtn.disabled = !loaded || busy || editing;
    saveBtn.disabled = !loaded || busy;
    cancelEditBtn.disabled = retryBtn.disabled = busy;
    outlineEditor.disabled = busy;
}
function setEditing(value) {
    editing = value;
    outlineView.classList[value ? "add" : "remove"]("hidden");
    outlineEditor.classList[value ? "remove" : "add"]("hidden");
    editBtn.classList[value ? "add" : "remove"]("hidden");
    for (const button of [saveBtn, cancelEditBtn]) button.classList[value ? "remove" : "add"]("hidden");
    updateControls();
}
async function read(response) {
    if (response.status === 202 && API.waitForJob) response = await API.waitForJob(response);
    let data;
    try { data = await response.json(); } catch { throw new Error("服务返回的数据无法读取，请重试。"); }
    if (!response.ok) throw new Error(data.message || "请求失败，请重试。");
    return data;
}
async function action(work, reload = false) {
    if (busy) return;
    busy = true;
    updateControls();
    notice.classList.add("hidden");
    retryBtn.classList.add("hidden");
    try { await work(); } catch (error) {
        notice.textContent = error.message || "连接中断，请检查网络后重试。";
        notice.classList.remove("hidden");
        if (reload) retryBtn.classList.remove("hidden");
    } finally { busy = false; updateControls(); }
}
async function loadOutline() {
    if (regenerationUnconfirmed) {
        const task = await read(await API.getTask(taskId));
        if (['running', 'queued'].includes(task.status)) throw new Error("任务仍在处理中，请稍后重新加载大纲。");
    }
    const data = await read(await API.getOutline(taskId));
    if (typeof data.markdown !== "string") throw new Error("大纲数据无法读取，请重试。");
    currentMarkdown = data.markdown;
    loaded = true;
    regenerationUnconfirmed = false;
    renderMarkdown(currentMarkdown);
    const mode = byId("outline-mode");
    if (mode) mode.textContent = data.recognition_mode === "deepseek" ? "已根据上传照片完成 DeepSeek 真实识别；请对照来源页校对。" : "旧记录或模拟内容：真实识别方式未确认。启用真实服务后可重新生成。";
    const quality = byId("outline-quality");
    if (quality) {
        const warnings = Array.isArray(data.warnings) ? data.warnings : [];
        quality.textContent = warnings.length ? "需要对照原图核对：\n" + warnings.join("\n") : "";
        quality.classList[warnings.length ? "remove" : "add"]("hidden");
    }
    setEditing(false);
}

function renderMarkdown(md) {
    const lines = [];
    for (const line of md.split("\n")) {
        if (line.startsWith("  ") && lines.length && lines[lines.length - 1].startsWith("- ")) lines[lines.length - 1] += "\n" + line.slice(2);
        else lines.push(line);
    }
    let html = "";
    let inList = false;
    for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) { if (inList) { html += "</ul>"; inList = false; } continue; }
        if (trimmed.startsWith("# ")) {
            if (inList) { html += "</ul>"; inList = false; }
            html += `<h1>${escapeHtml(trimmed.slice(2))}</h1>`;
        } else if (trimmed.startsWith("## ")) {
            if (inList) { html += "</ul>"; inList = false; }
            html += `<h2>${escapeHtml(trimmed.slice(3))}</h2>`;
        } else if (trimmed.startsWith("- ")) {
            if (!inList) { html += "<ul>"; inList = true; }
            html += `<li>${renderSourceTags(trimmed.slice(2))}</li>`;
        } else {
            if (inList) { html += "</ul>"; inList = false; }
            html += `<p>${escapeHtml(trimmed)}</p>`;
        }
    }
    if (inList) html += "</ul>";
    outlineView.innerHTML = html;
}

function renderSourceTags(text) {
    let result = escapeHtml(text);
    result = result.replace(/（来源：([^）]+)）/g, '<span class="source-tag">（来源：$1）</span>');
    result = result.replace(/（关联课堂页：([^）]+)）/g, '<span class="source-tag">（关联课堂页：$1）</span>');
    result = result.replace(/【课堂原话】/g, '<span class="type-original">【课堂原话】</span>');
    result = result.replace(/【AI推断】/g, '<span class="type-ai">【AI推断】</span>');
    result = result.replace(/【AI生成】/g, '<span class="type-ai">【AI生成】</span>');
    return result;
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}


editBtn.addEventListener("click", () => {
    if (busy || !loaded) return;
    outlineEditor.value = currentMarkdown;
    setEditing(true);
});
saveBtn.addEventListener("click", () => action(async () => {
    const draft = outlineEditor.value;
    await read(await API.updateOutline(taskId, draft));
    currentMarkdown = draft;
    renderMarkdown(currentMarkdown);
    setEditing(false);
}));
cancelEditBtn.addEventListener("click", () => { if (!busy) setEditing(false); });
regenerateBtn.addEventListener("click", () => {
    if (busy || !loaded || !confirm("重新生成将覆盖当前大纲及尚未保存的修改，是否继续？")) return;
    return action(async () => {
        loaded = false;
        regenerationUnconfirmed = true;
        updateControls();
        await read(await API.regenerate(taskId));
        regenerationUnconfirmed = false;
        await loadOutline();
    }, true);
});
exportBtn.addEventListener("click", () => {
    if (busy || !loaded || editing) return;
    return action(async () => {
        const response = await API.exportWord(taskId);
        if (!response.ok) await read(response);
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        try {
            const link = document.createElement("a");
            link.href = url;
            link.download = "outline_" + taskId + ".docx";
            link.click();
        } finally { URL.revokeObjectURL(url); }
    });
});
retryBtn.addEventListener("click", () => action(loadOutline, true));
updateControls();
return action(loadOutline, true);
})();
