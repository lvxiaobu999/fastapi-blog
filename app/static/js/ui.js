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
        button.dataset.idleAriaLabel = button.getAttribute("aria-label") ?? "";
        // 先记录 border-box 宽度，再让 loading 内容绝对定位。这样 spinner 和文案
        // 不会参与按钮的正常排版，也不会把“发送验证码”按钮横向撑大。
        button.style.width = `${Math.ceil(button.getBoundingClientRect().width)}px`;
        button.classList.add("button-is-loading");
        button.disabled = true;

        const spinner = document.createElement("span");
        spinner.className = "spinner-border spinner-border-sm";
        spinner.setAttribute("aria-hidden", "true");
        const label = document.createElement("span");
        label.textContent = loadingText;
        const loadingContent = document.createElement("span");
        loadingContent.className = "button-loading-content";
        loadingContent.append(spinner, label);
        button.replaceChildren(loadingContent);
        button.setAttribute("aria-label", loadingText);
        button.setAttribute("aria-busy", "true");
        return true;
    }

    if (button.dataset.loading !== "true") return false;
    // idleHtml 可能包含图标，因此恢复 innerHTML，而不是只恢复 textContent。
    button.innerHTML = button.dataset.idleHtml;
    button.disabled = false;
    button.style.width = "";
    button.classList.remove("button-is-loading");
    if (button.dataset.idleAriaLabel) {
        button.setAttribute("aria-label", button.dataset.idleAriaLabel);
    } else {
        button.removeAttribute("aria-label");
    }
    button.removeAttribute("aria-busy");
    delete button.dataset.loading;
    delete button.dataset.idleHtml;
    delete button.dataset.idleAriaLabel;
    return true;
}
