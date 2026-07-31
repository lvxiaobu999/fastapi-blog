/** 主题切换：图标按钮在浅色和深色之间直接切换，并保存用户选择。 */

// IIFE 创建私有作用域，避免 selectedTheme、themes 等名称污染全局 window。
(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    function applyTheme(theme) {
        // auto 只用于首次跟随系统；用户点击后会保存明确的 light 或 dark。
        const selected = ["light", "dark", "auto"].includes(theme) ? theme : "auto";
        const resolved = selected === "auto" ? (mediaQuery.matches ? "dark" : "light") : selected;

        document.documentElement.setAttribute("data-bs-theme", resolved);
        const hint = resolved === "dark" ? "切换到浅色主题" : "切换到深色主题";
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            // title 提供原生悬停提示，aria-label 为读屏软件说明同一个操作。
            button.title = hint;
            button.setAttribute("aria-label", hint);
        });
        return resolved;
    }

    $(function () {
        // 页面首次加载先恢复偏好；没有保存过时默认跟随系统。
        let selectedTheme = localStorage.getItem("blog-theme") || "auto";
        let resolvedTheme = applyTheme(selectedTheme);

        $("[data-theme-toggle]").on("click", function () {
            // 图标表达下一步操作：月亮切深色，太阳切浅色，不再打开选项菜单。
            selectedTheme = resolvedTheme === "dark" ? "light" : "dark";
            localStorage.setItem("blog-theme", selectedTheme);
            resolvedTheme = applyTheme(selectedTheme);
        });

        mediaQuery.addEventListener("change", () => {
            // 只有 auto 模式响应系统变化；手动选择的主题保持不变。
            if (selectedTheme === "auto") {
                resolvedTheme = applyTheme("auto");
            }
        });
    });
})();
