/** 在后台页面首次绘制前同步用户选择的主题。 */
(() => {
    const saved = localStorage.getItem("blog-theme") || "auto";
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute(
        "data-bs-theme",
        saved === "auto" ? (dark ? "dark" : "light") : saved,
    );
})();
