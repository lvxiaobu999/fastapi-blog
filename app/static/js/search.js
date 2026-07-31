/**
 * 全局文章标题搜索弹窗。
 *
 * 执行链：用户输入 -> 等待 300ms -> 取消旧请求 -> 查询标题 -> 渲染链接。
 * 防抖减少快速输入产生的请求数；请求版本号解决旧请求晚于新请求返回的竞态。
 */

const SEARCH_DELAY_MS = 300;

function debounce(callback, delay) {
    // timer 始终指向最后一次输入安排的任务，新输入会取消前一个任务重新计时。
    let timer = null;
    const debounced = (...args) => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => callback(...args), delay);
    };
    debounced.cancel = () => {
        // 弹窗关闭或关键词清空时，外部可取消尚未执行的 callback。
        window.clearTimeout(timer);
        timer = null;
    };
    return debounced;
}

$(function () {
    // 搜索组件并非每个页面都存在；缺少任一核心节点时不注册后续事件。
    const modalElement = document.querySelector("[data-search-modal]");
    const input = document.querySelector("[data-site-search]");
    const statusElement = document.querySelector("[data-search-status]");
    const resultsElement = document.querySelector("[data-search-results]");
    if (!modalElement || !input || !statusElement || !resultsElement) {
        return;
    }

    let pendingRequest = null;
    // 每启动一个请求就递增。响应只在版本仍为最新时才能修改页面。
    let requestVersion = 0;

    function clearResults(message = "输入关键词开始搜索") {
        resultsElement.replaceChildren();
        statusElement.textContent = message;
    }

    function renderResults(posts, keyword) {
        // 结果完全来自服务端，但仍使用 textContent 写标题，避免把标题当 HTML 执行。
        resultsElement.replaceChildren();
        if (!posts.length) {
            statusElement.textContent = `没有找到标题包含“${keyword}”的文章`;
            return;
        }

        const list = document.createElement("ul");
        list.className = "search-result-list";
        posts.forEach((post) => {
            const item = document.createElement("li");
            const link = document.createElement("a");
            link.className = "search-result-link";
            link.href = `/posts/${post.id}`;

            const title = document.createElement("span");
            title.textContent = post.title;
            const hint = document.createElement("span");
            hint.className = "search-result-hint";
            hint.textContent = "查看文章 →";

            link.append(title, hint);
            item.append(link);
            list.append(item);
        });
        resultsElement.append(list);
        statusElement.textContent = `找到 ${posts.length} 篇相关文章`;
    }

    const search = debounce((keyword) => {
        // abort 节省旧请求资源；version 是额外保险，处理 abort 前已返回等边界情况。
        pendingRequest?.abort();
        const currentVersion = ++requestVersion;
        statusElement.textContent = "正在搜索…";

        pendingRequest = $.ajax({
            url: "/api/posts/search",
            method: "GET",
            data: {keyword, limit: 8},
            dataType: "json",
        }).done((response) => {
            // 网络返回顺序不保证与输入顺序一致；只让最后一个关键词更新弹窗。
            if (currentVersion === requestVersion) {
                renderResults(response?.data ?? [], keyword);
            }
        }).fail((xhr) => {
            if (xhr.statusText !== "abort" && currentVersion === requestVersion) {
                clearResults("搜索暂时不可用，请稍后重试");
            }
        });
    }, SEARCH_DELAY_MS);

    $(input).on("input", function () {
        const keyword = this.value.trim();
        if (!keyword) {
            // 清空输入同时停止定时器和网络请求，版本递增使旧回调自动失效。
            search.cancel();
            pendingRequest?.abort();
            requestVersion += 1;
            clearResults();
            return;
        }
        search(keyword);
    }).on("keydown", function (event) {
        // Enter 直接进入第一条结果；没有结果时保留默认行为，不做跳转。
        if (event.key === "Enter") {
            const firstResult = resultsElement.querySelector("a");
            if (firstResult) {
                event.preventDefault();
                firstResult.click();
            }
        }
    });

    $(modalElement).on("shown.bs.modal", () => input.focus());
    $(modalElement).on("hidden.bs.modal", () => {
        // 关闭弹窗相当于结束本轮搜索，下次打开从干净状态开始。
        search.cancel();
        pendingRequest?.abort();
        pendingRequest = null;
        requestVersion += 1;
        input.value = "";
        clearResults();
    });

    // VuePress 风格快捷入口：Ctrl/Command + K 可随时打开；正文输入区域不劫持按键。
    $(document).on("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
            event.preventDefault();
            bootstrap.Modal.getOrCreateInstance(modalElement).show();
        }
    });
});
