(() => {
    const nativeFetch = globalThis.fetch.bind(globalThis);
    const storageKey = 'classroom-wechat-session';
    let redirecting = false;
    const current = () => { try { return JSON.parse(sessionStorage.getItem(storageKey) || 'null'); } catch (_) { return null; } };
    const getToken = () => { const value = current(); return value && value.token || ''; };
    function acceptSession(value) {
        if (!value || !/^[A-Za-z0-9_-]{40,256}$/.test(value.access_token) || !value.user || value.user.provider !== 'wechat' || !Number.isInteger(value.user.id) || value.user.id < 1) throw new Error('微信登录结果无效，请重新扫码。');
        sessionStorage.setItem(storageKey, JSON.stringify({ token: value.access_token, user: value.user }));
    }
    function clear() { sessionStorage.removeItem(storageKey); render(); }
    function loginUrl() { return '/login.html?next=' + encodeURIComponent((location.pathname || '/') + (location.search || '')); }
    function goToLogin() {
        if (!redirecting && location.pathname !== '/login.html' && typeof location.assign === 'function') { redirecting = true; location.assign(loginUrl()); }
    }
    async function authenticatedFetch(input, options = {}) {
        const target = new URL(String(input), location.origin);
        const protectedRequest = target.origin === location.origin && target.pathname.startsWith('/api/') && !['/api/meta', '/api/health', '/api/auth/wechat'].includes(target.pathname) && !target.pathname.startsWith('/api/auth/web/');
        if (!protectedRequest) return nativeFetch(input, options);
        const token = getToken();
        if (!token) {
            goToLogin();
            return new Response(JSON.stringify({ code: 'AUTH_REQUIRED', message: '请先使用微信扫码登录。', retryable: false }), { status: 401, headers: { 'Content-Type': 'application/json' } });
        }
        const headers = new Headers(options.headers || {});
        headers.set('Authorization', 'Bearer ' + token);
        const response = await nativeFetch(input, Object.assign({}, options, { headers }));
        if (response.status === 401) { clear(); goToLogin(); }
        return response;
    }
    globalThis.WorkspaceAuth = { fetch: authenticatedFetch, acceptSession, goToLogin, currentUser: () => { const value = current(); return value && value.user; } };
    function render() {
        if (typeof document === 'undefined') return;
        const box = document.getElementById('workspace-account');
        if (!box) return;
        box.querySelector('a').hidden = !!getToken();
        box.querySelector('a').href = loginUrl();
        box.querySelector('button').hidden = !getToken();
    }
    function mount() {
        sessionStorage.removeItem('classroom-access-token');
        if (document.getElementById('workspace-account') || location.pathname === '/login.html') return;
        const header = document.querySelector('.kf-header');
        if (!header) return;
        const box = document.createElement('div');
        box.id = 'workspace-account';
        box.style.cssText = 'margin-left:auto;display:flex;align-items:center;font-size:13px;white-space:nowrap';
        box.innerHTML = '<a style="color:inherit" href="/login.html">微信扫码登录</a><button type="button" style="font:inherit;color:inherit;background:transparent;border:0;cursor:pointer">退出登录</button>';
        header.append(box);
        box.querySelector('button').addEventListener('click', async () => {
            try { await authenticatedFetch('/api/auth/logout', { method: 'POST' }); }
            finally { clear(); location.assign('/login.html'); }
        });
        render();
    }
    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
        else mount();
    }
})();
