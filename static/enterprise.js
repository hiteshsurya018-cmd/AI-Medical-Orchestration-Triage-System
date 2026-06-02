(function enterpriseRuntime() {
    const root = document.documentElement;
    const body = document.body;
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const themeKey = "docq-theme";

    function applyTheme(theme) {
        body?.setAttribute("data-theme", theme);
        root.setAttribute("data-theme", theme);
        try {
            window.localStorage.setItem(themeKey, theme);
        } catch (error) {
            console.warn("theme persistence unavailable", error);
        }
    }

    function bootstrapTheme() {
        try {
            const stored = window.localStorage.getItem(themeKey);
            if (stored) {
                applyTheme(stored);
                return;
            }
        } catch (error) {
            console.warn("theme restore unavailable", error);
        }
        applyTheme(window.matchMedia?.("(prefers-color-scheme: light)")?.matches ? "light" : "dark");
    }

    function bindThemeToggle() {
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            button.addEventListener("click", () => {
                const nextTheme = (body?.getAttribute("data-theme") || "dark") === "dark" ? "light" : "dark";
                applyTheme(nextTheme);
            });
        });
    }

    function bindCommandPalette() {
        const palette = document.getElementById("command-palette");
        const openers = document.querySelectorAll("[data-command-open]");
        const closers = palette ? palette.querySelectorAll("[data-command-close]") : [];
        if (!palette) {
            return;
        }
        const toggle = (open) => {
            palette.classList.toggle("is-open", open);
            palette.setAttribute("aria-hidden", open ? "false" : "true");
        };
        openers.forEach((button) => button.addEventListener("click", () => toggle(true)));
        closers.forEach((button) => button.addEventListener("click", () => toggle(false)));
        document.addEventListener("keydown", (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
                event.preventDefault();
                toggle(true);
            }
            if (event.key === "Escape") {
                toggle(false);
            }
        });
    }

    function bindOperationalRail() {
        const links = Array.from(document.querySelectorAll(".command-nav-link"));
        if (links.length === 0) {
            return;
        }
        links.forEach((link) => {
            link.addEventListener("click", (event) => {
                const targetId = link.getAttribute("href");
                if (!targetId || !targetId.startsWith("#")) {
                    return;
                }
                const target = document.querySelector(targetId);
                if (!target) {
                    return;
                }
                event.preventDefault();
                target.scrollIntoView({ behavior: "smooth", block: "start" });
                links.forEach((item) => item.classList.remove("is-active"));
                link.classList.add("is-active");
            });
        });
    }

    function bindDemoBootstrap() {
        const trigger = document.querySelector("[data-demo-bootstrap]");
        const output = document.getElementById("demo-bootstrap-status");
        if (!trigger) {
            return;
        }
        trigger.addEventListener("click", async () => {
            trigger.setAttribute("disabled", "disabled");
            trigger.textContent = "Bootstrapping...";
            try {
                const response = await fetch("/api/demo/bootstrap", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrfToken,
                    },
                    body: JSON.stringify({}),
                });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload.error || "demo bootstrap failed");
                }
                if (output) {
                    output.textContent = `Demo ready: ${payload.appointment_count} appointments, ${payload.workflow_count} showcase workflows.`;
                }
            } catch (error) {
                if (output) {
                    output.textContent = `Demo bootstrap unavailable: ${error.message}`;
                }
            } finally {
                trigger.removeAttribute("disabled");
                trigger.textContent = "Bootstrap Demo";
            }
        });
    }

    bootstrapTheme();
    bindThemeToggle();
    bindCommandPalette();
    bindOperationalRail();
    bindDemoBootstrap();
})();
