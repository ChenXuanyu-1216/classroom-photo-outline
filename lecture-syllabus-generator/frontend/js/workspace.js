(() => {
    const root = document.getElementById("knowledge-flow-home");
    const main = document.querySelector(".kf-main");
    const nav = root?.querySelector(".kf-nav");
    if (!main || !nav) return;
    const links = [...nav.querySelectorAll(".kf-nav-item")];
    const paths = { upload: "/", history: "/history.html" };
    const route = pathname => pathname === "/" || pathname === "/index.html" ? "upload" : pathname === "/history.html" ? "history" : null;
    let current = route(window.location.pathname) || (root.classList.contains("kf-history-view") ? "history" : "upload");
    let navigation = Promise.resolve();
    let requestId = 0;
    const views = new Map([[current, { section: main.querySelector("section"), title: document.title, scroll: window.scrollY || 0 }]]);
    const marker = document.createElement("span");
    marker.className = "kf-nav-indicator";
    marker.setAttribute("aria-hidden", "true");
    nav.appendChild(marker);
    const draft = document.createElement("span");
    draft.className = "kf-draft-marker";
    draft.setAttribute("aria-hidden", "true");
    const uploadLink = links.find(link => route(new URL(link.href, window.location.href).pathname) === "upload");
    uploadLink?.appendChild(draft);

    // The offline preview embeds the second view instead of fetching it.
    const embeddedPreview = document.querySelector("template[data-workspace-page]") !== null;
    document.querySelectorAll("template").forEach(template => {
        const key = template.getAttribute("data-workspace-page");
        if (!paths[key] || views.has(key)) return;
        const section = template.content.querySelector("section");
        if (section) views.set(key, { section, title: template.getAttribute("data-page-title") || document.title, scroll: 0 });
    });
    function announce(text) {
        const status = document.getElementById("workspace-status");
        if (status) status.textContent = text;
    }
    function alignMarker() {
        const active = links.find(link => link.getAttribute("aria-current") === "page");
        if (!active) return;
        const bounds = active.getBoundingClientRect();
        marker.style.width = `${bounds.width}px`;
        marker.style.transform = `translateX(${bounds.left - nav.getBoundingClientRect().left}px)`;
    }
    function updateDraft() {
        const count = globalThis.ClassroomUpload?.getState().count || 0;
        draft.hidden = current !== "history" || !count;
        uploadLink?.setAttribute("aria-label", count && current === "history" ? `新建整理，保留了 ${count} 张待整理照片` : "新建整理");
    }
    function setNavigation(page) {
        links.forEach(link => {
            const active = route(new URL(link.href, window.location.href).pathname) === page;
            link.classList.toggle("kf-current", active);
            if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
        });
        alignMarker();
    }
    function animate(node, frames, duration) {
        if (!node?.animate) return Promise.resolve();
        return node.animate(frames, { duration, easing: "ease-in-out" }).finished.catch(() => {});
    }
    async function getView(page) {
        if (views.has(page)) return views.get(page);
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 8000);
        try {
            const response = await fetch(paths[page], { signal: controller.signal, credentials: "same-origin", headers: { Accept: "text/html" } });
            if (!response.ok) throw new Error("Page request failed");
            const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
            const section = parsed.querySelector(".kf-main")?.querySelector("section");
            if (!section?.querySelector(page === "upload" ? "#file-input" : "#task-list")) throw new Error("Invalid workspace page");
            const view = { section, title: parsed.title, scroll: 0 };
            views.set(page, view);
            return view;
        } finally { clearTimeout(timeout); }
    }
    async function performNavigation(page, { historyMode = "push" } = {}, request) {
        if (!paths[page]) return false;
        if (page === current) return true;
        if (globalThis.ClassroomUpload?.getState().isUploading) {
            announce("照片正在上传，请稍候再切换。");
            return false;
        }
        main.setAttribute("aria-busy", "true");
        root.classList.add("kf-navigating");
        try {
            let target;
            try { target = await getView(page); }
            catch { announce("暂时无法切换页面，请重试。当前照片和顺序已保留。"); return false; }
            if (request !== requestId) return true;
            if (globalThis.ClassroomUpload?.getState().isUploading) {
                announce("照片正在上传，请稍候再切换。");
                return false;
            }
            const previous = views.get(current);
            previous.scroll = window.scrollY || 0;
            const hadFocus = previous.section.contains(document.activeElement);
            // A short opacity-only transition also provides continuity with reduced motion.
            const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
            setNavigation(page);
            if (!globalThis.ClassroomBookUI?.turnWorkspace) await animate(previous.section, [{ opacity: 1 }, { opacity: 0 }], reducedMotion ? 120 : 160);
            if (request !== requestId || globalThis.ClassroomUpload?.getState().isUploading) {
                setNavigation(current);
                if (request !== requestId) return true;
                announce("照片正在上传，请稍候再切换。");
                return false;
            }
            main.replaceChildren(target.section);
            current = page;
            document.title = target.title;
            root.classList.toggle("kf-history-view", page === "history");
            globalThis.ClassroomUpload?.init();
            globalThis.ClassroomHistory?.init();
            updateDraft();
            if (historyMode === "push" && window.location.protocol !== "file:" && !embeddedPreview) {
                window.history.pushState({ ...window.history.state, kfWorkspace: page }, "", paths[page]);
            }
            const count = globalThis.ClassroomUpload?.getState().count || 0;
            announce(page === "upload" && count ? `已保留 ${count} 张照片和刚才的顺序，可以接着整理。` : page === "history" ? "可展开大纲快速回看，也可打开继续校对。" : "");
            window.scrollTo?.({ top: target.scroll, behavior: "instant" });
            if (globalThis.ClassroomBookUI?.turnWorkspace) await globalThis.ClassroomBookUI.turnWorkspace(previous.section, target.section, main, page);
            else await animate(target.section, [{ opacity: 0 }, { opacity: 1 }], reducedMotion ? 180 : 240);
            if (hadFocus) {
                const heading = target.section.querySelector("h1");
                heading?.setAttribute("tabindex", "-1");
                heading?.focus({ preventScroll: true });
            }
            return true;
        } finally {
            main.setAttribute("aria-busy", "false");
            root.classList.remove("kf-navigating");
        }
    }
    function navigate(page, options) {
        const request = ++requestId;
        const result = navigation.then(() => request !== requestId ? true : performNavigation(page, options, request));
        navigation = result.catch(() => false);
        return result;
    }
    document.addEventListener("click", event => {
        if (event.defaultPrevented || event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const link = event.target.closest?.("a");
        if (!link || link.getAttribute("download") !== null || (link.getAttribute("target") && link.getAttribute("target") !== "_self")) return;
        const url = new URL(link.href, window.location.href);
        const page = route(url.pathname);
        if (!page || url.origin !== window.location.origin || url.search || url.hash) return;
        event.preventDefault();
        navigate(page);
    });
    window.addEventListener("popstate", async () => {
        const page = route(window.location.pathname);
        if (page && !await navigate(page, { historyMode: "none" })) {
            window.history.replaceState({ ...window.history.state, kfWorkspace: current }, "", paths[current]);
        }
    });
    document.addEventListener("kf:files-changed", updateDraft);
    window.addEventListener("resize", alignMarker);
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(alignMarker).observe(nav);
    root.classList.add("kf-enhanced");
    setNavigation(current);
    updateDraft();
    globalThis.ClassroomWorkspace = { navigate };
})();
