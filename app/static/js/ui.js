/**
 * 通用按钮提交锁。
 *
 * setButtonLoading(button, true) 返回 false 表示按钮已经处于提交中，调用方应立即
 * return；恢复时会还原原始 HTML、宽度和无障碍状态。它只管理按钮视觉和锁状态，
 * 不负责发送请求，也不会自动在请求结束后恢复。
 */

export function setButtonLoading(button, loading, loadingText = "处理中…") {
    // 页面可能没有对应按钮，统一返回 false 让调用方停止后续提交逻辑。
    if (!button) return false;
    if (loading) {
        // dataset.loading 是同步互斥锁：连续点击只能有第一次进入请求逻辑。
        if (button.dataset.loading === "true") return false;
        button.dataset.loading = "true";
        button.dataset.idleHtml = button.innerHTML;
        // 切换成较短或较长的 loading 文案时固定当前宽度，避免周围布局跳动。
        button.style.width = `${button.getBoundingClientRect().width}px`;
        button.disabled = true;

        const spinner = document.createElement("span");
        spinner.className = "spinner-border spinner-border-sm";
        spinner.setAttribute("aria-hidden", "true");
        const label = document.createElement("span");
        label.textContent = loadingText;
        button.replaceChildren(spinner, label);
        button.setAttribute("aria-busy", "true");
        return true;
    }

    if (button.dataset.loading !== "true") return false;
    // idleHtml 可能包含图标，因此恢复 innerHTML，而不是只恢复 textContent。
    button.innerHTML = button.dataset.idleHtml;
    button.disabled = false;
    button.style.width = "";
    button.removeAttribute("aria-busy");
    delete button.dataset.loading;
    delete button.dataset.idleHtml;
    return true;
}
