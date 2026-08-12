/**
 * 在首次绘制前同步主题和登录状态，避免页面闪烁。
 *
 * 该文件保持为普通脚本，因为它需要在 HTML 绘制前执行；移出模板内联脚本后可以由
 * Content-Security-Policy 的 ``script-src 'self'`` 保护。
 */
(() => {
    const savedTheme = localStorage.getItem("blog-theme") || "auto";
    const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute(
        "data-bs-theme",
        savedTheme === "auto" ? (isDark ? "dark" : "light") : savedTheme,
    );
    document.documentElement.dataset.authState = localStorage.getItem("blog-access-token")
        ? "user"
        : "guest";
})();
