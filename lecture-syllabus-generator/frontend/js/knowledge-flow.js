(() => {
    const root = document.getElementById("knowledge-flow-home");
    const motionButton = root.querySelector(".kf-motion");
    const controls = root.querySelector(".kf-motion-controls");
    const speedButton = root.querySelector(".kf-speed-toggle");
    const speedPanel = root.querySelector(".kf-speed-panel");
    const speedInput = root.querySelector(".kf-speed-input");
    const speedValue = root.querySelector(".kf-speed-value");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let playing = !reducedMotion.matches;
    let motionChosen = false;
    const normalizeSpeed = value => {
        const number = Number(value);
        return Number.isFinite(number) && number > 0 ? Math.min(3, Math.max(0.25, number)) : 1;
    };
    let speed = 1;
    try { speed = normalizeSpeed(localStorage.getItem("knowledge-flow-speed")); } catch { /* Storage is optional. */ }

    function applySpeed() {
        speedInput.value = String(speed);
        speedValue.textContent = `${speed}×`;
        speedButton.textContent = `速度 ${speed}×`;
        speedInput.setAttribute("aria-valuetext", `${speed} 倍速度`);
        root.querySelectorAll(".kf-ribbon").forEach(ribbon => {
            if (typeof ribbon.getAnimations === "function") {
                // Preserve the current position while changing speed.
                ribbon.getAnimations().forEach(animation => animation.updatePlaybackRate(speed));
            } else {
                root.style.setProperty("--kf-pace", String(1 / speed));
            }
        });
    }

    function closeSpeed() {
        speedPanel.hidden = true;
        speedButton.setAttribute("aria-expanded", "false");
    }
    speedButton.addEventListener("click", () => {
        speedPanel.hidden = !speedPanel.hidden;
        speedButton.setAttribute("aria-expanded", String(!speedPanel.hidden));
    });
    speedInput.addEventListener("input", () => {
        speed = normalizeSpeed(speedInput.value);
        applySpeed();
        try { localStorage.setItem("knowledge-flow-speed", String(speed)); } catch { /* Still works without persistence. */ }
    });
    document.addEventListener("click", event => {
        if (!controls.contains(event.target)) closeSpeed();
    });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && !speedPanel.hidden) {
            closeSpeed();
            speedButton.focus();
        }
    });

    function render() {
        root.classList.toggle("kf-paused", !playing);
        root.classList.toggle("kf-playing", playing);
        motionButton.textContent = playing ? "暂停背景" : "播放背景";
        motionButton.setAttribute("aria-pressed", String(!playing));
        motionButton.disabled = false;
    }

    motionButton.addEventListener("click", () => {
        motionChosen = true;
        playing = !playing;
        render();
    });
    reducedMotion.addEventListener("change", () => {
        if (!motionChosen) playing = !reducedMotion.matches;
        render();
    });
    render();
    applySpeed();
})();
