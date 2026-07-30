/** 通用按钮提交状态；锁定重复点击，并保持按钮宽度避免布局抖动。 */

export function setButtonLoading(button, loading, loadingText = "处理中…") {
    if (!button) return false;
    if (loading) {
        if (button.dataset.loading === "true") return false;
        button.dataset.loading = "true";
        button.dataset.idleHtml = button.innerHTML;
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
    button.innerHTML = button.dataset.idleHtml;
    button.disabled = false;
    button.style.width = "";
    button.removeAttribute("aria-busy");
    delete button.dataset.loading;
    delete button.dataset.idleHtml;
    return true;
}
