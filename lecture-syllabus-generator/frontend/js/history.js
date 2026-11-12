(() => {
    const mounted = new WeakMap();
    function init() {
    const list = document.getElementById("task-list");
    if (!list) return Promise.resolve();
    if (mounted.has(list)) return mounted.get(list);
    const count = document.getElementById("task-count");
    let expanded = null;
    let records = [];
    const statuses = new Map([
        ["pending", "待处理"], ['queued', '排队中'], ["running", "处理中"], ["success", "已完成"],
        ["failed", "处理失败"], ["partial", "部分完成"],
    ]);

    function element(tag, className, text) {
        const node = document.createElement(tag);
        node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function showState(title, description, action) {
        const state = element("div", "kf-history-state");
        state.append(element("h3", "", title));
        if (description) state.append(element("p", "", description));
        if (action === "upload") {
            const link = element("a", "kf-history-action kf-history-primary", "新建整理");
            link.href = "/";
            state.append(link);
        } else if (action === "retry") {
            state.setAttribute("role", "alert");
            const retry = element("button", "kf-history-action kf-history-primary", "重新加载");
            retry.type = "button";
            retry.addEventListener("click", async () => {
                const hadFocus = document.activeElement === retry;
                await loadHistory();
                if (hadFocus && document.activeElement === document.body) {
                    const action = list.querySelector("button") || list.querySelector("a");
                    if (action) action.focus();
                }
            });
            state.append(retry);
        }
        list.replaceChildren(state);
    }

    function renderTask(task) {
        const record = element("article", "kf-record");
        const info = element("div", "kf-record-info");
        const heading = element("div", "kf-record-heading");
        const title = task.title && task.title !== "未命名任务" ? task.title : "课堂整理";
        const status = statuses.has(task.status) ? task.status : "unknown";
        heading.append(element("h3", "", title), element("span", `kf-status kf-status-${status}`, statuses.get(status) || "状态未知"));
        const meta = element("div", "kf-record-meta");
        // Existing API timestamps without an offset are stored as UTC.
        const timestamp = String(task.created_at);
        const date = new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp) ? timestamp : `${timestamp}Z`);
        const time = element("time", "", Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", {
            year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
        }));
        if (!Number.isNaN(date.getTime())) time.dateTime = date.toISOString();
        meta.append(element("span", "", `${task.page_count} 页`), time, element("span", "", `任务 #${task.task_id}`));
        const paper = element("span", "kf-record-paper");
        paper.setAttribute("aria-hidden", "true");
        paper.append(element("span", ""), element("span", ""), element("span", ""));
        const text = element("div", "kf-record-text");
        text.append(heading, meta);
        info.append(paper, text);
        const actions = element("div", "kf-record-actions");
        const id = encodeURIComponent(String(task.task_id));
        const preview = element("button", "kf-history-action kf-record-preview", "展开大纲");
        preview.type = "button";
        preview.setAttribute("aria-expanded", "false");
        preview.setAttribute("aria-controls", `outline-preview-${id}`);
        preview.setAttribute("aria-label", `展开${title}的大纲，任务 ${task.task_id}`);
        const panel = element("div", "kf-outline-preview");
        panel.id = `outline-preview-${id}`;
        panel.hidden = true;
        panel.setAttribute("role", "region");
        panel.setAttribute("aria-label", `${title}的大纲预览`);
        let loaded = false;
        let pending = null;
        let deleting = false;
        function close() {
            panel.hidden = true;
            preview.textContent = "展开大纲";
            preview.setAttribute("aria-expanded", "false");
            preview.setAttribute("aria-label", `展开${title}的大纲，任务 ${task.task_id}`);
        }
        function loadPreview() {
            if (pending) return pending;
            panel.setAttribute("aria-busy", "true");
            panel.replaceChildren(element("p", "kf-preview-message", "正在读取已保存的大纲…"));
            pending = (async () => {
                try {
                    const response = await API.getOutline(task.task_id);
                    if (!response.ok) throw new Error("Outline request failed");
                    const data = await response.json();
                    if (typeof data.markdown !== "string" || !data.markdown.trim()) throw new Error("Invalid outline");
                    const lines = data.markdown.trim().split(/\r?\n/).filter(line => line.trim());
                    const nodes = lines.slice(0, 20).map(line => element(/^#\s/.test(line) ? "h4" : /^#{2,6}\s/.test(line) ? "h5" : "p", "kf-preview-line", line.replace(/^#{1,6}\s+/, "").replace(/^[-*]\s+/, "").slice(0, 500)));
                    nodes.push(element("p", "kf-preview-caption", lines.length > 20 || lines.some(line => line.length > 500) ? "内容较长，此处显示部分预览。可打开查看完整大纲。" : "已保存的大纲预览，可打开继续校对。"));
                    panel.replaceChildren(...nodes);
                    loaded = true;
                } catch {
                    const retry = element("button", "kf-history-action kf-record-open", "重新读取大纲");
                    retry.type = "button";
                    retry.addEventListener("click", async () => {
                        const hadFocus = document.activeElement === retry;
                        await loadPreview();
                        if (hadFocus && !panel.hidden && document.activeElement === document.body) {
                            (panel.querySelector("button") || preview).focus();
                        }
                    });
                    panel.replaceChildren(element("p", "kf-preview-message", "暂时无法预览大纲。大纲可能尚未生成，请稍后重试，或打开任务查看。"), retry);
                } finally {
                    panel.setAttribute("aria-busy", "false");
                    pending = null;
                }
            })();
            return pending;
        }
        preview.addEventListener("click", () => {
            if (deleting) return;
            if (!panel.hidden) { close(); expanded = null; return; }
            if (expanded) expanded();
            expanded = close;
            panel.hidden = false;
            preview.textContent = "收起大纲";
            preview.setAttribute("aria-expanded", "true");
            preview.setAttribute("aria-label", `收起${title}的大纲，任务 ${task.task_id}`);
            if (!loaded) return loadPreview();
        });
        const open = element("a", "kf-history-action kf-record-open", "打开");
        open.href = `/outline.html?task_id=${id}`;
        open.setAttribute("aria-label", `打开${title}，任务 ${task.task_id}`);
        const exportLink = element("a", "kf-history-action kf-record-export", "导出 Word");
        exportLink.href = `/api/tasks/${id}/export`;
        exportLink.setAttribute("aria-label", `导出${title}为 Word，任务 ${task.task_id}`);
        exportLink.addEventListener('click', async event => {
            event.preventDefault();
            if (deleting) return;
            try {
                const response = await API.exportWord(task.task_id);
                if (!response.ok) throw new Error('导出失败，请检查登录和大纲状态。');
                const fileUrl = URL.createObjectURL(await response.blob());
                const download = document.createElement('a');
                download.href = fileUrl;
                download.download = `outline_${task.task_id}.docx`;
                document.body.appendChild(download);
                download.click();
                download.remove();
                setTimeout(() => URL.revokeObjectURL(fileUrl), 1000);
            } catch (error) { window.alert(error.message || '导出失败，请稍后重试。'); }
        });
        const remove = element("button", "kf-history-action kf-record-delete", "删除");
        remove.type = "button";
        remove.setAttribute("aria-label", `删除${title}，任务 ${task.task_id}`);
        remove.disabled = ['running', 'queued'].includes(task.status);
        if (remove.disabled) remove.title = "任务正在处理中，完成后可删除";
        const deleteMessage = element("p", "kf-delete-message");
        deleteMessage.hidden = true;
        deleteMessage.setAttribute("role", "alert");
        remove.addEventListener("click", async () => {
            if (deleting || ['running', 'queued'].includes(task.status)) return;
            if (!window.confirm(`删除“${title}”（任务 #${task.task_id}）、已保存的大纲及关联评价证据？删除后无法恢复，请先导出所需证据。`)) return;
            const hadFocus = record.contains(document.activeElement);
            deleting = true;
            remove.disabled = true;
            remove.textContent = "删除中…";
            preview.disabled = true;
            record.setAttribute("aria-busy", "true");
            deleteMessage.hidden = true;
            try {
                const response = await API.deleteTask(task.task_id);
                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    throw new Error(error.message || "删除失败，请稍后重试。");
                }
                const siblings = [...list.children];
                const index = siblings.indexOf(record);
                const focusTarget = siblings[index + 1] || siblings[index - 1];
                const restoreFocus = hadFocus && (record.contains(document.activeElement) || document.activeElement === document.body);
                if (expanded === close) expanded = null;
                record.remove();
                records = records.filter(row => row !== record);
                count.textContent = `${records.length} 条记录`;
                if (!records.length) showState("还没有整理记录", "添加课堂照片后，整理记录会保存在这里。", "upload");
                if (restoreFocus && document.activeElement === document.body) {
                    const next = focusTarget?.querySelector(".kf-record-delete");
                    (next && !next.disabled ? next : focusTarget?.querySelector("button") || list.querySelector("a"))?.focus();
                }
            } catch (error) {
                deleteMessage.textContent = error.message?.includes("删除") ? error.message : "删除失败，请检查网络后重试。";
                deleteMessage.hidden = false;
                remove.disabled = false;
                remove.textContent = "删除";
                preview.disabled = false;
                deleting = false;
                record.setAttribute("aria-busy", "false");
            }
        });
        actions.append(preview, open, exportLink, remove);
        const top = element("div", "kf-record-top");
        top.append(info, actions);
        record.append(top, deleteMessage, panel);
        return record;
    }

    async function loadHistory() {
        list.setAttribute("aria-busy", "true");
        count.textContent = "读取中";
        showState("正在读取整理记录", "");
        try {
            const response = await API.listTasks();
            if (!response.ok) throw new Error("History request failed");
            const data = await response.json();
            if (!Array.isArray(data.tasks)) throw new Error("Invalid history response");
            records = data.tasks.map(renderTask);
            count.textContent = `${records.length} 条记录`;
            if (records.length) list.replaceChildren(...records);
            else showState("还没有整理记录", "添加课堂照片后，整理记录会保存在这里。", "upload");
        } catch {
            count.textContent = "未加载";
            showState("暂时无法加载记录", "请检查网络连接，然后重新加载。", "retry");
        } finally {
            list.setAttribute("aria-busy", "false");
        }
    }

    const ready = loadHistory();
    mounted.set(list, ready);
    return ready;
    }
    globalThis.ClassroomHistory = { init };
    return init();
})();
