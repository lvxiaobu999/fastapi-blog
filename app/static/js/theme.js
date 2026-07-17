(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const themes = {
        light: { label: "Light", icon: "☀" },
        dark: { label: "Dark", icon: "☾" },
        auto: { label: "Auto", icon: "◐" },
    };

    function applyTheme(theme) {
        const selected = themes[theme] ? theme : "auto";
        const resolved = selected === "auto" ? (mediaQuery.matches ? "dark" : "light") : selected;

        document.documentElement.setAttribute("data-bs-theme", resolved);
        $("#themeIcon").text(themes[selected].icon);
        $("#themeLabel").text(themes[selected].label);
        $(".theme-option").removeClass("active").filter(`[data-theme="${selected}"]`).addClass("active");
    }

    $(function () {
        let selectedTheme = localStorage.getItem("blog-theme") || "auto";
        applyTheme(selectedTheme);

        $(".theme-option").on("click", function () {
            selectedTheme = $(this).data("theme");
            localStorage.setItem("blog-theme", selectedTheme);
            applyTheme(selectedTheme);
        });

        mediaQuery.addEventListener("change", () => {
            if (selectedTheme === "auto") {
                applyTheme("auto");
            }
        });
    });
})();
