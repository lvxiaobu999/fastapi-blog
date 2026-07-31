/** 主题切换：保存用户选择，并在“自动”模式下跟随操作系统颜色主题。 */

// IIFE 创建私有作用域，避免 selectedTheme、themes 等名称污染全局 window。
(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const themes = {
        light: { label: "Light", icon: "☀" },
        dark: { label: "Dark", icon: "☾" },
        auto: { label: "Auto", icon: "◐" },
    };

    function applyTheme(theme) {
        // 非法或历史遗留值统一回退到 auto，保证后面的对象读取始终安全。
        const selected = themes[theme] ? theme : "auto";
        // selected 是用户偏好；resolved 是 Bootstrap 最终需要的 light/dark。
        const resolved = selected === "auto" ? (mediaQuery.matches ? "dark" : "light") : selected;

        document.documentElement.setAttribute("data-bs-theme", resolved);
        $("#themeIcon").text(themes[selected].icon);
        $("#themeLabel").text(themes[selected].label);
        $(".theme-option").removeClass("active").filter(`[data-theme="${selected}"]`).addClass("active");
    }

    $(function () {
        // 页面首次加载先恢复偏好；没有保存过时默认跟随系统。
        let selectedTheme = localStorage.getItem("blog-theme") || "auto";
        applyTheme(selectedTheme);

        $(".theme-option").on("click", function () {
            // 点击只保存 light/dark/auto 偏好，auto 的实际明暗会动态计算。
            selectedTheme = $(this).data("theme");
            localStorage.setItem("blog-theme", selectedTheme);
            applyTheme(selectedTheme);
        });

        mediaQuery.addEventListener("change", () => {
            // 只有 auto 模式响应系统变化；手动选择的主题保持不变。
            if (selectedTheme === "auto") {
                applyTheme("auto");
            }
        });
    });
})();
