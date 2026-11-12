(() => {
    const taskId = new URLSearchParams(window.location.search).get("task_id");
    if (!/^[1-9]\d*$/.test(taskId || "")) { window.location.href = "/"; return; }
    const byId = id => document.getElementById(id);
    if (byId('evaluation-link')) byId('evaluation-link').href = '/evaluation.html?task_id=' + taskId;
    const retryButton = byId("retry-btn");
    const continueButton = byId("continue-btn");
    const viewButton = byId("view-outline");
    let busy = false;
    let failedPages = [];
    let hasOutline = false;
    let observedRunning = false;
    let generationPending = false;
    const repairableConfig = page => /AUTH|CONFIG|BALANCE|BUDGET/.test(page.error_code || '');
    const canRetry = page => page.error_retryable !== false || repairableConfig(page);
    function show(id, visible) { byId(id).classList[visible ? "remove" : "add"]("hidden"); }
    function step(id, state, label) {
        const node = byId(`step-${id}`);
        node.classList.remove("active", "done");
        if (state) node.classList.add(state);
        node.querySelector(".step-status").textContent = label || (state === "done" ? "完成" : "等待中");
    }
    async function read(response) {
        if (response.status === 202 && API.waitForJob) {
            try { response = await API.waitForJob(response, displayJob); }
            finally { observedRunning = false; await refreshPages(); }
        }
        let data;
        try { data = await response.json(); } catch { throw new Error("服务返回的数据无法读取，请重试。"); }
        if (!response.ok) {
            const error = new Error(data.message || "请求失败，请重试。");
            error.retryable = data.retryable !== false;
            throw error;
        }
        return data;
    }
    function displayJob(job) {
        observedRunning = ['queued', 'running'].includes(job.status);
        byId('progress-title').textContent = job.status === 'queued' ? '任务已排队' : '后台处理中';
        byId('progress-message').textContent = job.status === 'queued'
            ? '已保存处理请求，轮到本任务后自动开始；刷新页面不会重复提交。'
            : '后台正在处理图片，离开页面后任务仍会继续。';
        show('retry-btn', false);
    }
    async function guarded(action) {
        if (busy) return;
        busy = true;
        retryButton.disabled = continueButton.disabled = true;
        show("progress-error", false);
        show("continue-btn", false);
        try { await action(); } catch (error) {
            for (const id of ['recognize', 'extract', 'cluster', 'generate']) {
                if (byId(`step-${id}`).classList.contains('active')) step(id, '', '未完成');
            }
            byId("progress-title").textContent = "处理暂未完成";
            byId("progress-error-message").textContent = error.message || "连接中断，请检查网络后重试。";
            show("progress-error", true);
            show("continue-btn", error.retryable !== false);
            continueButton.textContent = "重新检查并继续";
        } finally {
            busy = false;
            retryButton.disabled = continueButton.disabled = false;
        }
    }
    function displayPages(pages) {
        if (!Array.isArray(pages)) throw new Error("页级数据不完整，请重新检查。");
        failedPages = pages.filter(page => page.failed_flag);
        const lowPages = pages.filter(page => page.low_confidence_flag);
        for (const [id, entries, failed] of [["low-confidence-pages", lowPages, false], ["failed-page-list", failedPages, true]]) {
            const list = byId(id);
            list.replaceChildren();
            for (const page of entries) {
                const item = document.createElement("li");
                const next = page.error_code === 'IMAGE_UNREADABLE' || (page.error_code || '').startsWith('IMAGE_')
                    ? '请更换清晰图片后重新上传。'
                    : page.error_retryable === false ? '请先检查模型配置、密钥或账户余额后再处理。' : '可稍后重试本页。';
                item.textContent = `P${page.page_no}（${page.filename}）${failed ? `：${page.error_msg || "识别失败"} ${next}` : ""}`;
                list.appendChild(item);
            }
        }
        show("low-confidence-warning", lowPages.length > 0);
        show("failed-pages", failedPages.length > 0);
        retryButton.textContent = failedPages.some(repairableConfig) ? '修正配置后重试失败页' : '重试失败页';
        show("retry-btn", failedPages.some(canRetry) && !observedRunning);
    }
    async function refreshPages() {
        const data = await read(await API.getPages(taskId));
        displayPages(data.pages);
        return data.pages;
    }
    function recognitionFailed() {
        step('recognize', '', '识别失败');
        byId('progress-title').textContent = '页面识别失败';
        byId('progress-message').textContent = '已保留失败记录。恢复连接后点击“重试失败页”继续处理，无需重新上传。';
    }
    function completed() {
        hasOutline = true;
        for (const id of ["recognize", "extract", "cluster", "generate"]) step(id, "done");
        if (failedPages.length) step("recognize", "done", "部分完成");
        byId("progress-title").textContent = failedPages.length ? "部分页面已整理" : "整理完成";
        byId("progress-message").textContent = failedPages.length ? "已保留成功页面的结果，可查看大纲或重试失败页。" : "大纲已保存，可继续校对和导出。";
        viewButton.href = `/outline.html?task_id=${taskId}`;
        show("view-outline", true);
    }
    async function generate(includeRecognition = false) {
        generationPending = true;
        step("recognize", includeRecognition ? 'active' : 'done', includeRecognition ? '后台识别与整理中…' : undefined);
        // The backend combines these stages and does not report separate progress.
        step("extract", "active", "抽取、归组与生成中…");
        step("cluster", "", "由后端统一处理");
        step("generate", "", "由后端统一处理");
        byId("progress-message").textContent = "正在整理课堂内容并生成大纲；启用真实 AI 服务时会补充带标注的拓展知识。";
        await read(await API.runPipeline(taskId));
        generationPending = false;
        completed();
    }
    async function restore(allowRetry = false) {
        const task = await read(await API.getTask(taskId));
        if (!task.status) throw new Error("任务状态无法读取，请重新检查。");
        observedRunning = ['running', 'queued'].includes(task.status);
        await refreshPages();
        if (task.active_job && API.waitForJob) {
            const saved = await API.getOutline(taskId);
            if (saved.ok) {
                hasOutline = true;
                viewButton.href = `/outline.html?task_id=${taskId}`;
                viewButton.textContent = '查看已保存大纲';
                show('view-outline', true);
            }
            const response = await API.waitForJob({ status: 202, json: async () => task.active_job }, displayJob);
            observedRunning = false;
            await refreshPages();
            await read(response);
            return restore(false);
        }
        const outline = await API.getOutline(taskId);
        if (outline.ok) {
            const saved = await read(outline);
            if (typeof saved.markdown !== "string") throw new Error("大纲数据无法读取，请重新检查。");
            hasOutline = true;
            viewButton.href = `/outline.html?task_id=${taskId}`;
            viewButton.textContent = "查看已保存大纲";
            show("view-outline", true);
            if (generationPending && task.status === "success") {
                generationPending = false;
                completed();
                byId("progress-title").textContent = "已恢复服务器保存的大纲";
                byId("progress-message").textContent = "上次响应未确认，现已读取服务器当前版本；需要重新生成时，请在大纲页确认操作。";
                return;
            }
            if (observedRunning || task.status === "failed" || generationPending) {
                byId("progress-title").textContent = observedRunning ? "任务正在处理" : "上次生成未完成或结果未确认";
                byId("progress-message").textContent = (task.error_msg || "请检查处理状态。") + "已有大纲已保留。";
                continueButton.textContent = observedRunning ? "刷新处理状态" : "重试生成";
                show("continue-btn", true);
                if (allowRetry && !observedRunning && confirm("重新生成将覆盖已保存的大纲，是否继续？")) await generate();
                return;
            }
            completed();
            return;
        }
        // Only the explicit existing contract means no outline has been generated.
        const absent = await outline.json();
        if (absent.code !== "PIPELINE_ERROR") {
            const error = new Error(absent.message || "无法查询已有大纲，请重新检查。");
            error.retryable = absent.retryable !== false;
            throw error;
        }
        hasOutline = false;
        show("view-outline", false);
        if (observedRunning) {
            byId("progress-title").textContent = task.status === 'queued' ? '任务已排队' : "任务正在处理";
            byId("progress-message").textContent = "后台处理中，请稍后检查状态。";
            continueButton.textContent = "刷新处理状态";
            show("continue-btn", true);
            return;
        }
        if (failedPages.length) {
            recognitionFailed();
            return;
        }
        if (task.status === "failed" && !allowRetry) {
            byId("progress-title").textContent = "上次生成未完成";
            byId("progress-message").textContent = task.error_msg || "已保留页面数据，可手动继续生成。";
            show("continue-btn", true);
            return;
        }
        if (task.status === "pending") {
            if (API.backgroundJobs) return generate(true);
            step("recognize", "active", "识别中…");
            await read(await API.recognize(taskId));
            const pages = await refreshPages();
            if (pages.every(page => page.failed_flag)) {
                recognitionFailed();
                byId('progress-title').textContent = '所有页面识别失败';
                return;
            }
        }
        await generate();
    }
    retryButton.addEventListener("click", () => {
        if (busy) return;
        if (hasOutline && !confirm("重试失败页后将重新生成并覆盖当前大纲，是否继续？")) return;
        return guarded(async () => {
            const task = await read(await API.getTask(taskId));
            if (['running', 'queued'].includes(task.status)) {
                observedRunning = true;
                show("retry-btn", false);
                show("continue-btn", true);
                byId("progress-message").textContent = "后台处理中，请稍后检查状态。";
                return;
            }
            observedRunning = false;
            await refreshPages();
            const retryPages = failedPages.filter(canRetry);
            if (!retryPages.length) return;
            step("recognize", "active", "重试失败页…");
            for (const page of retryPages) await read(await API.recognize(taskId, page.page_no));
            const pages = await refreshPages();
            if (pages.every(page => page.failed_flag)) {
                recognitionFailed();
                return;
            }
            await generate();
        });
    });
    continueButton.addEventListener("click", () => guarded(() => restore(true)));
    return guarded(restore);
})();
