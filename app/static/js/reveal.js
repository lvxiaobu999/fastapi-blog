/** 滚动渐显：元素进入视口时添加 is-visible，配合 site.css 里的 .reveal 过渡。 */

(() => {
    const root = document.documentElement;

    // 先标记“已启用”，CSS 才会把 .reveal 初始设为透明；禁用 JS 时内容保持可见。
    root.classList.add("reveal-ready");

    const elements = document.querySelectorAll(".reveal");
    if (!elements.length) return;

    // 用户偏好减少动态效果或浏览器不支持 IntersectionObserver 时，直接全部显示。
    if (
        window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
        !("IntersectionObserver" in window)
    ) {
        elements.forEach((el) => el.classList.add("is-visible"));
        return;
    }

    const observer = new IntersectionObserver(
        (entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.classList.add("is-visible");
                    // 每个元素只触发一次，进入视口后停止观察，避免后续滚动重复计算。
                    observer.unobserve(entry.target);
                }
            });
        },
        // 元素露出 12% 即触发；底部留 8% 余量让渐显在完全进入前就开始。
        { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
    );

    elements.forEach((el) => observer.observe(el));
})();
