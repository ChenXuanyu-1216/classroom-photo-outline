(function (root) {
    function safeReturn(value, origin) {
        try {
            const url = new URL(value || '/history.html', origin);
            if (url.origin === origin && ['/', '/index.html', '/history.html', '/progress.html', '/outline.html', '/evaluation.html'].includes(url.pathname)) return url.pathname + url.search;
        } catch (_) { /* Invalid destinations return to the records page. */ }
        return '/history.html';
    }
    function createFlow({ request, onChange, onSuccess, setTimer = setTimeout, clearTimer = clearTimeout, now = Date.now }) {
        let generation = 0, timer, scene, expires = 0, disposed = false;
        function stop() { clearTimer(timer); timer = null; }
        async function poll(version) {
            if (disposed || version !== generation) return;
            stop();
            if (now() >= expires) { onChange({ status: 'expired' }); return; }
            try {
                const value = await request('poll', { scene });
                if (disposed || version !== generation) return;
                if (value.status === 'confirmed') { onChange({ status: 'confirmed' }); onSuccess(value); return; }
                if (!['waiting', 'scanned'].includes(value.status)) { onChange(value); return; }
                onChange(value);
                timer = setTimer(() => poll(version), 1500);
            } catch (error) {
                if (!disposed && version === generation) onChange({ status: 'error', message: error.message });
            }
        }
        return {
            async refresh() {
                if (disposed) return;
                const version = ++generation;
                stop();
                const previous = scene;
                scene = null;
                onChange({ status: 'loading' });
                try {
                    if (previous) await request('cancel', { scene: previous }).catch(() => {});
                    if (disposed || version !== generation) return;
                    const value = await request('challenges');
                    if (disposed || version !== generation) return;
                    scene = value.scene;
                    expires = now() + value.expires_in * 1000;
                    onChange(Object.assign({}, value, { status: 'waiting' }));
                    timer = setTimer(() => poll(version), 1500);
                } catch (error) {
                    if (!disposed && version === generation) onChange({ status: 'error', message: error.message });
                }
            },
            dispose() { disposed = true; generation++; stop(); },
            resume() { disposed = false; scene = null; return this.refresh(); },
        };
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = { safeReturn, createFlow };
    if (typeof document === 'undefined') return;
    const panel = document.getElementById('qr-panel'), image = document.getElementById('login-qr');
    const status = document.getElementById('login-status'), hint = document.getElementById('login-hint');
    const refresh = document.getElementById('refresh-qr'), verification = document.getElementById('verification');
    const destination = safeReturn(new URLSearchParams(location.search).get('next'), location.origin);
    const states = {
        loading: ['正在准备登录码', '请稍候…'], waiting: ['使用微信扫一扫', '扫码后，请在小程序中确认登录。'],
        scanned: ['已扫码，等待确认', '请核对手机上的校验码，再点击“确认登录”。'],
        confirmed: ['登录成功', '正在打开你的课堂资料…'], expired: ['登录码已过期', '请刷新二维码，重新扫码。'],
        denied: ['已取消登录', '你的账号尚未登录此网页，可重新扫码。'], cancelled: ['登录已取消', '需要时可重新获取二维码。'],
        used: ['此登录码已使用', '请刷新二维码后重新登录。'], error: ['暂时无法登录', '请稍后重试。'],
    };
    async function request(path, body) {
        const response = await fetch('/api/auth/web/' + path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || '登录服务暂时不可用，请重试。');
        return data;
    }
    const flow = createFlow({ request,
        onChange(value) {
            const text = states[value.status] || states.error;
            panel.dataset.state = value.status;
            status.textContent = text[0]; hint.textContent = value.message || text[1];
            if (value.qr_image) image.src = value.qr_image;
            image.hidden = !['waiting', 'scanned', 'confirmed'].includes(value.status);
            verification.textContent = value.verification ? '校验码 ' + value.verification : verification.textContent;
            verification.hidden = !['waiting', 'scanned'].includes(value.status);
            refresh.disabled = ['loading', 'confirmed'].includes(value.status);
            refresh.textContent = ['waiting', 'scanned'].includes(value.status) ? '刷新二维码' : '重新获取二维码';
        },
        onSuccess(value) {
            try { root.WorkspaceAuth.acceptSession(value); location.replace(destination); }
            catch (error) { status.textContent = '无法保存登录状态'; hint.textContent = error.message + ' 请允许此网站使用会话存储后重试。'; refresh.disabled = false; }
        },
    });
    refresh.addEventListener('click', () => flow.refresh());
    window.addEventListener('pagehide', () => flow.dispose());
    window.addEventListener('pageshow', event => { if (event.persisted) flow.resume(); });
    flow.refresh();
})(globalThis);
