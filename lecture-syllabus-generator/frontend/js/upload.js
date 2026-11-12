(() => {
const mounted = new WeakMap();
let current = null;
function init() {
const fileInput = document.getElementById("file-input");
if (!fileInput) return;
if (mounted.has(fileInput)) return mounted.get(fileInput);
let selectedFiles = [];
let isUploading = false;
let rows = new Map();
const previewUrls = new Map();

const dropZone = document.getElementById("drop-zone");
const fileList = document.getElementById("file-list");
const fileCount = document.getElementById("file-count");
const generateBtn = document.getElementById("generate-btn");
const message = document.getElementById("upload-message");
const modelStatus = document.getElementById("model-status");
const budgetNote = document.getElementById("model-budget-note");
const modelRetry = document.getElementById("model-retry");
let modelBlocked = Boolean(modelStatus);
let modelChecking = false;

async function refreshModel() {
    if (!modelStatus || modelChecking) return;
    modelChecking = true;
    modelBlocked = true;
    modelStatus.textContent = "正在检查识别服务…";
    if (modelRetry) modelRetry.disabled = true;
    const controller = new AbortController();
    let timeout;
    try {
        const meta = await Promise.race([
            (async () => {
                if (typeof API.getMeta !== "function") throw new Error("页面脚本需要更新");
                const response = await API.getMeta({ signal: controller.signal });
                if (!response.ok) throw new Error("配置无法读取");
                return response.json();
            })(),
            new Promise((_, reject) => {
                timeout = setTimeout(() => { controller.abort(); reject(new Error("检查超时")); }, 8000);
            }),
        ]);
        const live = meta.model_provider === "deepseek";
        modelBlocked = !meta.model_ready || (live && !meta.model_budget?.available);
        modelStatus.textContent = !meta.model_ready ? "识别服务尚未配置完成，请先检查本地密钥。" :
            live && !meta.model_budget?.available ? "模型调用额度不足，请先核对费用后调整额度。" :
            live ? "DeepSeek 真实识别 · 提取照片知识点，按主题生成复习框架" : "模拟演示模式 · 当前返回预设内容";
        if (budgetNote) budgetNote.textContent = !live ? "" : meta.model_budget.enforced === false ?
            "提交后照片将发送至 DeepSeek 云端识别。未设置本地额度上限；实际费用以 DeepSeek 账户账单为准。" :
            `提交后照片将发送至 DeepSeek 云端识别。累计保守调用额度 ${meta.model_budget.cap_cny} 元，已预留 ${meta.model_budget.reserved_cny ?? "待核对"} 元。`;
        if (Array.isArray(meta.allowed_ext)) {
            fileInput.setAttribute("accept", meta.allowed_ext.join(","));
            const formats = document.getElementById("file-formats");
            if (formats) formats.textContent = meta.allowed_ext.map(ext => ext.slice(1).toUpperCase()).join(" / ");
        }
        const hint = document.getElementById("file-hint");
        if (hint) hint.textContent = `最多 ${meta.max_files_per_batch} 张，单张不超过 ${meta.max_file_mb} MB`;
        if (modelRetry) modelRetry.hidden = !modelBlocked;
    } catch {
        modelBlocked = true;
        modelStatus.textContent = "暂时无法连接识别服务或检查超时，请在后端启动后重试；若仍无响应，请刷新页面。";
        if (budgetNote) budgetNote.textContent = "已选照片和顺序会保留，检查服务不会调用付费模型。";
        if (modelRetry) modelRetry.hidden = false;
    } finally {
        clearTimeout(timeout);
        modelChecking = false;
        if (modelRetry) modelRetry.disabled = false;
        renderFileList();
    }
}
modelRetry?.addEventListener("click", refreshModel);

function announce(text, error = false) {
    if (!message) return;
    message.setAttribute("role", error ? "alert" : "status");
    message.textContent = text;
}

dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (!isUploading) dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener("change", (e) => handleFiles(e.target.files));

function handleFiles(files) {
    if (isUploading || !files.length) return;
    const added = Array.from(files);
    selectedFiles.push(...added);
    fileInput.value = "";
    renderFileList(true);
    announce(`已添加 ${added.length} 张照片，共 ${selectedFiles.length} 张。切换到整理记录后，照片和顺序会保留。`);
}

function renderFileList(animate = false) {
    const positions = new Map([...rows].map(([file, row]) => [file, row.getBoundingClientRect().top]));
    rows = new Map();
    fileList.replaceChildren();
    fileCount.textContent = `${selectedFiles.length} 张`;
    generateBtn.disabled = isUploading || modelBlocked || selectedFiles.length === 0;
    generateBtn.textContent = isUploading ? "上传中..." : "生成大纲";
    fileInput.disabled = isUploading;
    dropZone.setAttribute("aria-disabled", String(isUploading));
    if (selectedFiles.length === 0) {
        const empty = document.createElement("p");
        empty.className = "kf-empty";
        empty.textContent = "还没有添加照片";
        fileList.appendChild(empty);
    }
    selectedFiles.forEach((file, index) => {
        const item = document.createElement("div");
        item.className = "kf-file-row";
        const order = document.createElement("span");
        order.className = "file-order";
        order.textContent = `P${index + 1}`;
        const name = document.createElement("span");
        name.className = "file-name";
        name.textContent = file.name;
        const controls = document.createElement("div");
        controls.className = "move-btns";
        const actions = [
            ["↑", `上移第 ${index + 1} 张照片`, () => moveFile(index, -1), index === 0],
            ["↓", `下移第 ${index + 1} 张照片`, () => moveFile(index, 1), index === selectedFiles.length - 1],
            ["✕", `删除第 ${index + 1} 张照片`, () => removeFile(index), false],
        ];
        actions.forEach(([text, label, handler, disabled]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = text;
            button.setAttribute("aria-label", label);
            button.disabled = isUploading || disabled;
            button.addEventListener("click", handler);
            controls.appendChild(button);
        });
        if (typeof URL !== "undefined" && typeof URL.createObjectURL === "function" && file.type?.startsWith("image/")) {
            if (!previewUrls.has(file)) previewUrls.set(file, URL.createObjectURL(file));
            const image = document.createElement("img");
            image.className = "book-upload-thumb";
            image.src = previewUrls.get(file);
            image.alt = file.name;
            item.appendChild(image);
        }
        item.append(order, name, controls);
        fileList.appendChild(item);
        rows.set(file, item);
        if (animate && item.animate && !globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
            const previous = positions.get(file);
            const delta = previous === undefined ? 12 : previous - item.getBoundingClientRect().top;
            item.animate([{ opacity: previous === undefined ? 0 : 1, transform: `translateY(${delta}px)` }, { opacity: 1, transform: "translateY(0)" }], {
                duration: 260, delay: previous === undefined ? Math.min(index * 35, 140) : 0, easing: "cubic-bezier(.22,.85,.26,1)",
            });
        }
    });
    document.dispatchEvent(new CustomEvent("kf:files-changed"));
}

function restoreFocus(file, hadFocus, action = 2) {
    if (!hadFocus) return;
    const actions = rows.get(file)?.querySelectorAll("button");
    const next = actions && (!actions[action]?.disabled ? actions[action] : [...actions].find(button => !button.disabled));
    (next || fileInput).focus();
}

function moveFile(index, dir) {
    if (isUploading) return;
    const newIndex = index + dir;
    if (newIndex < 0 || newIndex >= selectedFiles.length) return;
    const hadFocus = fileList.contains(document.activeElement);
    const file = selectedFiles[index];
    [selectedFiles[index], selectedFiles[newIndex]] = [selectedFiles[newIndex], selectedFiles[index]];
    renderFileList(true);
    restoreFocus(file, hadFocus, dir > 0 ? 1 : 0);
    announce(`已移到第 ${newIndex + 1} 页，来源页码已更新。`);
}

function removeFile(index) {
    if (isUploading) return;
    const hadFocus = fileList.contains(document.activeElement);
    const removed = selectedFiles.splice(index, 1)[0];
    if (!selectedFiles.includes(removed) && previewUrls.has(removed)) { URL.revokeObjectURL(previewUrls.get(removed)); previewUrls.delete(removed); }
    renderFileList(true);
    restoreFocus(selectedFiles[Math.min(index, selectedFiles.length - 1)], hadFocus);
    announce(selectedFiles.length ? `已移除照片，还剩 ${selectedFiles.length} 张。` : "照片列表已清空，可以重新添加。");
}

current = { get count() { return selectedFiles.length; }, get isUploading() { return isUploading; } };
mounted.set(fileInput, current);
renderFileList();
refreshModel();

generateBtn.addEventListener("click", async () => {
    if (isUploading || modelBlocked || selectedFiles.length === 0) return;
    isUploading = true;
    dropZone.classList.remove("dragover");
    renderFileList();
    announce("正在上传课堂照片，请稍候。");
    const order = selectedFiles.map((_, i) => i + 1).join(",");
    try {
        const resp = await API.createTask(selectedFiles, order);
        const data = await resp.json();
        if (resp.status === 201) {
            globalThis.ClassroomBookUI?.rememberTask?.(data.task_id);
            window.location.href = `/progress.html?task_id=${data.task_id}`;
            return;
        } else {
            announce(`上传失败：${data.message || "未知错误"}。照片已保留，可以重试。`, true);
        }
    } catch (e) {
        announce("网络错误，照片已保留，请重试。", true);
    }
    isUploading = false;
    renderFileList();
});
return current;
}
globalThis.ClassroomUpload = { init, getState: () => ({ count: current?.count || 0, isUploading: current?.isUploading || false }) };
init();
})();
