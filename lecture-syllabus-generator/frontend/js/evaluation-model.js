(function (root) {
    'use strict';
    const flowLabels = { upload: '照片上传', generate: '生成大纲', source: '来源对照', edit_save: '编辑与保存', reopen: '重新打开', word: 'Word 导出', source_labels: '用户辨认来源标签' };
    const statusLabels = { passed: '通过', failed: '失败', not_run: '未执行', blocked: '受阻' };
    function reviewInput(data, modules = []) {
        const value = key => String(data.get(key) || '').trim();
        const names = ['expected_count', 'matched', 'output', 'supported', 'source_correct', 'source_total'];
        const values = names.map(value);
        let counts = null;
        if (values.some(Boolean)) {
            if (values.some(v => !/^\d+$/.test(v) || Number(v) > 100000)) throw new Error('内容计数请完整填写六项非负整数，或全部留空。');
            counts = Object.fromEntries(names.map((name, i) => [name === 'expected_count' ? 'expected' : name, Number(values[i])]));
            if (counts.matched > counts.expected || counts.supported > counts.output || counts.source_correct > counts.source_total || counts.matched > counts.supported) throw new Error('正确数不能超过对应总数；正确恢复数不能超过有证据支持的输出数。');
        }
        const errors = ['error_category', 'error_expected', 'error_actual', 'error_impact'].map(value);
        if (errors.some(Boolean) && errors.some(v => !v)) throw new Error('关键错误请填写类别、正确内容、实际片段和影响四项。');
        const module = value('scope_type') === 'module';
        const selectedModule = module ? modules.find(item => item.id === value('module_id')) : null;
        if (module && !selectedModule) throw new Error('请选择这份快照中实际存在的模块。');
        return {
            reviewer: value('reviewer'), review_kind: value('review_kind'),
            scope: module ? 'module:' + selectedModule.heading : value('scope_type') + ':' + value('scope_name'),
            module_id: selectedModule?.id || null,
            expected: value('expected'), observed: value('observed'), counts,
            structure: module && value('structure') !== '' ? Number(value('structure')) : null,
            usability: module && value('usability') !== '' ? Number(value('usability')) : null,
            critical_errors: errors.some(Boolean) ? [{category: errors[0], expected: errors[1], actual: errors[2], impact: errors[3]}] : [],
            notes: value('notes'), user_feedback: value('user_feedback'),
            core_flow: Object.fromEntries(Object.keys(flowLabels).map(key => [key, value('flow_' + key) || 'not_run'])),
        };
    }
    const ratio = value => value == null ? '未评分／无比率' : (value * 100).toFixed(1) + '%';
    const quoted = text => String(text || '未记录').split('\n').map(line => '> ' + line).join('\n');
    function report(snapshot) {
        const e = snapshot.evidence, job = e.job;
        let text = `# EXP05 效果评价与错误证据\n\n任务：${e.task.id}\n\n快照：${snapshot.id}\n\n取证时间：${snapshot.created_at}\n\n证据 SHA-256：${snapshot.sha256}\n\n状态：${job?.status || e.task.status}；方式：${e.capture_kind}\n\n`;
        text += `完整流程墙钟时间：${job?.elapsed_seconds == null ? '未观测' : job.elapsed_seconds + ' 秒'}（不是单次 HTTP 超时配置）\n\n`;
        if (e.configuration) text += '识别配置：' + e.configuration.recognizer + '；归组配置：' + e.configuration.clusterer + '。配置不等于实际执行，各页实际元数据见 JSON。\n\n';
        if (['mock', 'fallback'].includes(e.configuration?.recognizer)) text += '**模拟／兜底配置：仅用于流程检查，不能证明真实识别质量。**\n\n';
        text += '本报告保留每份人工记录，不自动汇总为总体准确率或 EXP05 通过结论。JSON 导出包含完整识别、配置及代码/提示词哈希；原图需另行保存。\n\n';
        text += '## 页级执行结果\n\n';
        for (const p of e.pages) text += `- P${p.page_no}：${p.failed ? '失败' : p.structured && Object.keys(p.structured).length ? '已有识别结果，须核对是否复用' : '未观测到识别结果'}；输入哈希 ${p.input_sha256 || '未取得'}；错误 ${p.error.code || '无已记录错误'}\n`;
        text += '\n## 人工评价\n\n';
        if (!snapshot.reviews.length) text += '尚无人工评价；不能宣称内容质量通过。\n\n';
        for (const r of snapshot.reviews) {
            text += `### 记录 ${r.id}\n\n评审者与范围\n\n${quoted(r.reviewer + ' / ' + r.review_kind + ' / ' + r.scope)}\n\n保存时间：${r.created_at}\n\n期望与依据\n\n${quoted(r.expected)}\n\n实际输出与证据\n\n${quoted(r.observed)}\n\n`;
            text += `忠实度：${ratio(r.metrics?.fidelity)}；覆盖率：${ratio(r.metrics?.coverage)}；来源正确率：${ratio(r.metrics?.source)}\n\n原始计数：${r.counts ? JSON.stringify(r.counts) : '未填写'}\n\n结构：${r.structure ?? '未评分'}；复习可用性：${r.usability ?? '未评分'}；单模块判定：${r.module_pass == null ? '未判定' : r.module_pass ? '满足本协议口径' : '不满足'}\n\n`;
            if (r.scope.startsWith('module:')) text += r.module_binding === 'bound' ? `快照内模块 ID：${r.module_id}\n\n` : '未绑定快照中的真实模块，不能给出模块通过判定。\n\n';
            for (const error of r.critical_errors) text += `关键错误\n\n${quoted(error.category + '\n正确内容：' + error.expected + '\n实际片段：' + error.actual + '\n影响：' + error.impact)}\n\n`;
            for (const [key, status] of Object.entries(r.core_flow || {})) text += `- ${flowLabels[key] || key}：${statusLabels[status] || status}\n`;
            text += `\n用户反馈\n\n${quoted(r.user_feedback)}\n\n补充／修订理由\n\n${quoted(r.notes)}\n\n`;
        }
        text += '## 该轮保存稿\n\n' + quoted(e.outline?.markdown || '无已保存大纲') + '\n\n';
        text += '## 证据限制\n\n' + e.limits.map(item => '- ' + item).join('\n') + '\n';
        return text;
    }
    const model = { reviewInput, ratio, report, flowLabels, statusLabels };
    root.Exp05 = model;
    if (typeof module !== 'undefined') module.exports = model;
})(globalThis);
