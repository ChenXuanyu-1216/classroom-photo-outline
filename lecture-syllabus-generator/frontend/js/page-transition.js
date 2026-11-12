(() => {
    const documentRoot = document.documentElement;
    documentRoot.classList.add("kf-page-entering");

    function init() {
        const main = document.querySelector(".kf-main");
        if (!main) {
            documentRoot.classList.remove("kf-page-entering");
            return;
        }
        const reducedMotion = () => globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        let departing = false;
        let departure;
        let requestId = 0;

        function cancelDeparture() {
            requestId++;
            departure?.cancel();
            departure = null;
            departing = false;
            main.setAttribute("aria-busy", "false");
        }

        function enter() {
            cancelDeparture();
            const entry = main.animate?.([{ opacity: 0 }, { opacity: 1 }], {
                duration: reducedMotion() ? 180 : 240,
                easing: "ease-in-out",
                fill: "backwards",
            });
            documentRoot.classList.remove("kf-page-entering");
            entry?.finished.catch(() => {});
        }

        document.addEventListener("click", async event => {
            if (event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            const link = event.target.closest?.("a");
            if (!link || link.getAttribute("download") !== null || (link.getAttribute("target") && link.getAttribute("target") !== "_self")) return;
            const url = new URL(link.href, window.location.href);
            if (url.origin !== window.location.origin || !["http:", "https:"].includes(url.protocol) || url.hash) return;
            const workspacePath = ["/", "/index.html", "/history.html"].includes(url.pathname);
            // The workspace router may already have handled this newer choice.
            if (departing && workspacePath) cancelDeparture();
            if (event.defaultPrevented || url.href === window.location.href) return;
            const opensOutline = url.pathname === "/outline.html";
            const returnsToWorkspace = window.location.pathname === "/outline.html" && workspacePath;
            if (!opensOutline && !returnsToWorkspace) return;
            event.preventDefault();
            if (departing) return;
            departing = true;
            const request = ++requestId;
            main.setAttribute("aria-busy", "true");
            departure = main.animate?.([{ opacity: 1 }, { opacity: 0 }], {
                duration: reducedMotion() ? 120 : 160,
                easing: "ease-in-out",
                fill: "forwards",
            });
            if (departure) await departure.finished.catch(() => {});
            if (request !== requestId) return;
            window.location.assign(url.href);
        });

        window.addEventListener("popstate", () => { if (departing) cancelDeparture(); });
        window.addEventListener("pagehide", () => { requestId++; });
        // A cached document retains its finished departure animation when Back restores it.
        window.addEventListener("pageshow", event => { if (event.persisted) enter(); });
        enter();
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
    else init();
})();
