(() => {
    'use strict';
    const task = new URLSearchParams(location.search).get('task_id');
    if (!/^[1-9]\d*$/.test(task || '')) { location.href = '/history.html'; return; }
    const byId = id => document.getElementById(id);
    const form = byId('review-form'), select = byId('snapshot-select');
    let current = null, busy = false, imageUrl = null, imageGeneration = 0, dirty = false, boundSnapshotId = null;
    let editRevision = 0;
    byId('back-outline').href = '/outline.html?task_id=' + task;
    byId('back-progress').href = '/progress.html?task_id=' + task;
    const endpoint = '/api/tasks/' + task + '/evaluations';
    const fetchAuth = (url, options) => WorkspaceAuth.fetch(url, {cache: 'no-store', ...options});
    function notify(message, error = false) {
        const node = byId('evaluation-notice'); node.textContent = message; node.dataset.error = String(error);
    }
    function controls(value) {
        busy = value;
        for (const id of ['capture-evidence', 'refresh-evidence']) byId(id).disabled = value;
        select.disabled = value || !select.value;
        for (const id of ['export-evidence', 'export-report', 'save-review']) byId(id).disabled = value || !current;
    }
    async function action(work) {
        if (busy) return;
        controls(true);
        try { await work(); } catch (error) { notify(error.message || '连接中断，请刷新记录后重试。', true); }
        finally { controls(false); }
    }
    async function read(response) {
        let data;
        try { data = await response.json(); } catch { throw new Error('服务返回的数据无法读取，请刷新记录。'); }
        if (!response.ok) {
            if (response.status === 422) throw new Error(data.message || '评价字段无效，请核对计数、评分范围及必填证据。');
            throw new Error(data.message || '请求失败，请重试。');
        }
        return data;
    }
    function option(value, text) { const node = document.createElement('option'); node.value = value; node.textContent = text; return node; }
    function localTime(time) { return time ? new Date(time).toLocaleString('zh-CN') : '时间未记录'; }
    async function loadList(preferred, discardApproved = false) {
        const data = await read(await fetchAuth(endpoint));
        if (!Array.isArray(data.snapshots)) throw new Error('证据列表格式无效。');
        const target = data.snapshots.some(row => row.id === preferred) ? preferred : data.snapshots[0]?.id;
        if (boundSnapshotId && target !== boundSnapshotId && !discardApproved && !mayLeaveDraft()) return;
        select.replaceChildren();
        for (const row of data.snapshots) select.append(option(row.id, `${localTime(row.created_at)} · ${row.status} · ${row.capture_kind === 'job_terminal' ? '作业终态' : '手动保留'}`));
        if (data.snapshots.length) {
            select.value = target;
            await loadSnapshot();
        } else {
            current = null; select.append(option('', '还没有证据快照'));
            byId('evaluation-content').hidden = true;
            byId('snapshot-summary').textContent = '旧任务可点击“保留当前状态”。该操作只读取已有结果，不调用模型；无法补回历史首次输出。';
            notify('还没有可评价的记录。');
        }
    }
    async function loadSnapshot() {
        current = null; byId('evaluation-content').hidden = true;
        const data = await read(await fetchAuth(endpoint + '/' + encodeURIComponent(select.value)));
        if (!data.evidence || !Array.isArray(data.reviews)) throw new Error('证据记录格式无效。');
        if (boundSnapshotId !== data.id) {
            form.reset(); byId('module-scores').disabled = true; dirty = false;
        }
        boundSnapshotId = data.id;
        current = data;
        const modules = byId('scope-module'), selectedModule = modules.value;
        modules.replaceChildren(option('', current.modules?.length ? '请选择本轮输出中的模块' : '本轮没有可绑定的模块'));
        for (const [index, module] of (current.modules || []).entries()) modules.append(option(module.id, `${index + 1}. ${module.heading}${module.has_content ? '' : '（无正文）'}`));
        modules.value = (current.modules || []).some(module => module.id === selectedModule) ? selectedModule : '';
        syncScope();
        const evidence = data.evidence, job = evidence.job;
        const simulated = ['mock', 'fallback'].includes(evidence.configuration.recognizer);
        byId('snapshot-summary').textContent = `${localTime(data.created_at)} · ${job?.status || evidence.task.status}\n${simulated ? '模拟／兜底配置：此记录仅用于流程检查，不能证明真实识别质量。\n' : ''}${job?.elapsed_seconds == null ? '整轮耗时未观测' : '整轮耗时 ' + job.elapsed_seconds.toFixed(2) + ' 秒'}\nSHA-256：${data.sha256}`;
        byId('snapshot-provenance').textContent = JSON.stringify({configuration: evidence.configuration, job, prompt_hashes: evidence.prompt_hashes, limits: evidence.limits}, null, 2);
        byId('snapshot-outline').textContent = evidence.outline?.markdown || '这份快照没有已保存的大纲。可记录执行失败，不应视为内容通过。';
        byId('output-origin').textContent = evidence.outline?.origin === 'retained_before_attempt' ? '这是本轮失败前保留的旧稿，不能作为本轮成功输出。' : evidence.capture_kind === 'manual_current_state' ? '手动保留的已保存版本，可能包含人工修改。' : '作业结束时的保存稿；请结合本轮状态和各页识别记录判断。';
        const pages = byId('source-page'); pages.replaceChildren();
        for (const page of evidence.pages) pages.append(option(String(page.page_no), 'P' + page.page_no + (page.failed ? ' · 失败' : '')));
        const requestedPage = new URLSearchParams(location.search).get('page_no');
        if (evidence.pages.some(page => String(page.page_no) === requestedPage)) pages.value = requestedPage;
        byId('evaluation-content').hidden = false;
        renderReviews(); await loadSource();
        notify(`已读取该轮快照；${data.reviews.length ? '已有 ' + data.reviews.length + ' 份人工记录。' : '尚未评分。'}`);
    }
    async function loadSource() {
        const generation = ++imageGeneration;
        if (imageUrl) { URL.revokeObjectURL(imageUrl); imageUrl = null; }
        const image = byId('source-photo'); image.hidden = true; image.removeAttribute('src');
        const page = current?.evidence.pages.find(p => String(p.page_no) === byId('source-page').value);
        if (!page) { byId('source-status').textContent = '没有来源页。'; return; }
        byId('source-recognition').textContent = JSON.stringify({error: page.error, metadata: page.metadata, text: page.text, structured: page.structured}, null, 2);
        byId('source-status').textContent = '正在读取原图…';
        try {
            const response = await fetchAuth('/api/tasks/' + task + '/pages/' + page.page_no + '/image');
            if (!response.ok) await read(response);
            const blob = await response.blob();
            if (!blob.type.startsWith('image/')) throw new Error('来源不是可预览的图片。');
            if (generation !== imageGeneration) return;
            if (page.input_sha256 && globalThis.crypto?.subtle) {
                const hash = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
                if (generation !== imageGeneration) return;
                const actual = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
                if (actual !== page.input_sha256) throw new Error('当前原图与快照哈希不同，不能用它作为本轮评分依据。');
            }
            imageUrl = URL.createObjectURL(blob); image.src = imageUrl; image.alt = '来源 P' + page.page_no + ' 的原始照片'; image.hidden = false;
            image.onerror = () => { image.hidden = true; byId('source-status').textContent = '原图无法解码，请核对图片文件；不能据此评分。'; };
            byId('source-status').textContent = 'P' + page.page_no + (page.input_sha256 ? ' · 快照已登记输入哈希' : ' · 未取得取证时输入哈希，请人工核对');
        } catch (error) { if (generation === imageGeneration) byId('source-status').textContent = error.message || '原图暂时无法读取，可切换页码重试。'; }
    }
    function renderReviews() {
        const list = byId('saved-reviews'); list.replaceChildren();
        if (!current.reviews.length) { const p = document.createElement('p'); p.textContent = '尚无人工评价。'; list.append(p); }
        for (const review of current.reviews) {
            const article = document.createElement('article'); article.className = 'evaluation-review';
            const title = document.createElement('h3'); title.textContent = review.reviewer + ' · ' + review.scope + ' · ' + {self:'自评', independent:'独立复核', adjudication:'裁决'}[review.review_kind];
            const detail = document.createElement('p'); detail.textContent = `${localTime(review.created_at)}\n忠实度 ${Exp05.ratio(review.metrics?.fidelity)} · 覆盖率 ${Exp05.ratio(review.metrics?.coverage)} · 来源 ${Exp05.ratio(review.metrics?.source)}\n关键错误 ${review.critical_errors.length} 项 · 单模块${review.module_pass == null ? '未判定' : review.module_pass ? '满足本协议口径' : '不满足本协议口径'}${review.module_binding === 'unbound' ? '\n未绑定快照中的真实模块，不能给出模块通过判定。' : ''}`;
            const errors = review.critical_errors.map(e => `${e.category}\n正确内容：${e.expected}\n实际片段：${e.actual}\n影响：${e.impact}`).join('\n\n');
            const flow = Object.entries(review.core_flow || {}).map(([key, state]) => `${Exp05.flowLabels[key]}：${Exp05.statusLabels[state]}`).join('\n');
            const evidence = document.createElement('pre'); evidence.textContent = '期望：' + review.expected + '\n实际：' + review.observed + '\n原始计数：' + (review.counts ? JSON.stringify(review.counts) : '未填写') + '\n关键错误：\n' + (errors || '未记录') + '\n流程检查：\n' + (flow || '未记录') + '\n补充：' + (review.notes || '未记录') + '\n用户反馈：' + (review.user_feedback || '未记录');
            article.append(title, detail, evidence); list.append(article);
        }
    }
    function download(blob, filename) {
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    for (const [key, text] of Object.entries(Exp05.flowLabels)) {
        const label = document.createElement('label'); label.textContent = text;
        const input = document.createElement('select'); input.name = 'flow_' + key;
        for (const status of ['not_run', 'passed', 'failed', 'blocked']) input.append(option(status, Exp05.statusLabels[status]));
        label.append(input); byId('core-flow-fields').append(label);
    }
    function syncScope() {
        const module = byId('scope-type').value === 'module';
        byId('module-scores').disabled = !module;
        byId('scope-module-label').hidden = !module;
        byId('scope-module').disabled = !module;
        byId('scope-module').required = module;
        byId('scope-name-label').hidden = module;
        byId('scope-name').disabled = module;
        byId('scope-name').required = !module;
    }
    byId('scope-type').addEventListener('change', syncScope);
    function changed() { dirty = true; editRevision++; }
    form.addEventListener('input', changed);
    form.addEventListener('change', changed);
    function mayLeaveDraft() { return !dirty || confirm('有尚未保存的评价。切换记录将清空这些内容，是否继续？'); }
    select.addEventListener('change', () => {
        if (!mayLeaveDraft()) { select.value = boundSnapshotId || ''; return; }
        return action(loadSnapshot);
    });
    byId('source-page').addEventListener('change', loadSource);
    byId('refresh-evidence').addEventListener('click', () => action(() => loadList(boundSnapshotId || current?.id)));
    byId('capture-evidence').addEventListener('click', () => {
        if (!mayLeaveDraft()) return;
        return action(async () => {
        const captured = await read(await fetchAuth(endpoint, {method:'POST'})); await loadList(captured.id, true);
        });
    });
    form.addEventListener('submit', event => {
        event.preventDefault();
        return action(async () => {
            if (!current) throw new Error('请先选择证据记录。');
            const submittedRevision = editRevision;
            const body = Exp05.reviewInput(new FormData(form), current.modules || []);
            const saved = await read(await fetchAuth(endpoint + '/' + current.id + '/reviews', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}));
            current.reviews.push(saved); renderReviews();
            dirty = editRevision !== submittedRevision;
            notify(dirty ? '提交时的评价已保存；保存期间的新增修改尚未保存，请再次保存。' : '评价已追加保存。需要修订时请说明理由后再提交，原记录会保留。');
        });
    });
    byId('export-evidence').addEventListener('click', () => action(async () => {
        const response = await fetchAuth(endpoint + '/' + current.id + '/export'); if (!response.ok) await read(response);
        download(await response.blob(), 'exp05-' + current.id + '.json'); notify('证据已导出；原图请另行保存。');
    }));
    byId('export-report').addEventListener('click', () => action(async () => {
        current = await read(await fetchAuth(endpoint + '/' + current.id)); renderReviews();
        download(new Blob([Exp05.report(current)], {type:'text/markdown;charset=utf-8'}), 'exp05-' + current.id + '.md'); notify('评价报告已导出。');
    }));
    window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
    window.addEventListener('pagehide', () => { if (imageUrl) URL.revokeObjectURL(imageUrl); });
    return action(() => loadList());
})();
