const authenticatedFetch = (input, options) => globalThis.WorkspaceAuth ? globalThis.WorkspaceAuth.fetch(input, options) : fetch(input, options);
const API = {
    backgroundJobs: true,
    async createTask(files, order) {
        const formData = new FormData();
        files.forEach(f => formData.append("files", f));
        if (order) formData.append("order", order);
        const resp = await authenticatedFetch("/api/tasks", { method: "POST", body: formData });
        return resp;
    },
    async getTask(taskId) {
        const resp = await authenticatedFetch(`/api/tasks/${taskId}`, { cache: 'no-store' });
        return resp;
    },
    async getPages(taskId) {
        const resp = await authenticatedFetch(`/api/tasks/${taskId}/pages`, { cache: 'no-store' });
        return resp;
    },
    async recognize(taskId, pageNo) {
        const url = pageNo ? `/api/tasks/${taskId}/recognize?page_no=${pageNo}` : `/api/tasks/${taskId}/recognize`;
        const resp = await authenticatedFetch(url, { method: "POST", headers: { Prefer: 'respond-async' } });
        return resp;
    },
    async runPipeline(taskId) {
        const resp = await authenticatedFetch(`/api/tasks/${taskId}/pipeline`, { method: "POST", headers: { Prefer: 'respond-async' } });
        return resp;
    },
    async getOutline(taskId) {
        const resp = await authenticatedFetch(`/api/tasks/${taskId}/outline`);
        return resp;
    },
    async updateOutline(taskId, markdown) {
        const resp = await authenticatedFetch(`/api/tasks/${taskId}/outline`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ markdown }),
        });
        return resp;
    },
    async regenerate(taskId) {
        const resp = await authenticatedFetch(`/api/tasks/${taskId}/regenerate`, { method: "POST", headers: { Prefer: 'respond-async' } });
        return resp;
    },
    async exportWord(taskId) {
        const resp = await authenticatedFetch(`/api/tasks/${taskId}/export`);
        return resp;
    },
    async listTasks() {
        const resp = await authenticatedFetch("/api/tasks");
        return resp;
    },
    async deleteTask(taskId) {
        return authenticatedFetch(`/api/tasks/${encodeURIComponent(String(taskId))}`, { method: "DELETE" });
    },
    async getMeta({ signal } = {}) {
        const resp = await authenticatedFetch("/api/meta", { signal, cache: "no-store" });
        return resp;
    },
    async waitForJob(response, onStatus = () => {}) {
        if (response.status !== 202) return response;
        const submitted = await response.json();
        if (!submitted.job_id || !submitted.task_id) throw new Error('后台处理记录不完整，请重新检查状态。');
        const result = (body, status = 200) => new Response(JSON.stringify(body), {
            status, headers: { 'Content-Type': 'application/json' },
        });
        onStatus(submitted);
        for (let poll = 0; poll < 400; poll++) {
            const latest = await authenticatedFetch(`/api/tasks/${encodeURIComponent(submitted.task_id)}/jobs/${encodeURIComponent(submitted.job_id)}`, { cache: 'no-store' });
            if (!latest.ok) return latest;
            const job = await latest.json();
            onStatus(job);
            if (job.status === 'failed') return result(job.error || {
                code: 'JOB_FAILED', message: '后台处理失败，请查看页级原因。', retryable: true }, 502);
            if (job.status === 'success' || job.status === 'partial') return result(job.result);
            if (!['queued', 'running'].includes(job.status)) throw new Error('后台状态无法读取，请重新检查。');
            await new Promise(resolve => setTimeout(resolve, 1500));
        }
        return result({ code: 'JOB_WAIT_TIMEOUT', message: '后台仍可能处理中，请重新检查状态；无需重新上传。', retryable: true }, 504);
    },
};
