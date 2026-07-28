/** 全局文章标题搜索弹窗；输入防抖后请求轻量搜索接口，并处理过期请求竞争。 */

const SEARCH_DELAY_MS = 300;

function debounce(callback, delay) {
    let timer = null;
    const debounced = (...args) => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => callback(...args), delay);
    };
    debounced.cancel = () => {
        window.clearTimeout(timer);
        timer = null;
    };
    return debounced;
}

$(function () {
    const modalElement = document.querySelector("[data-search-modal]");
    const input = document.querySelector("[data-site-search]");
    const statusElement = document.querySelector("[data-search-status]");
    const resultsElement = document.querySelector("[data-search-results]");
    if (!modalElement || !input || !statusElement || !resultsElement) {
        return;
    }

    let pendingRequest = null;
    let requestVersion = 0;

    function clearResults(message = "输入关键词开始搜索") {
        resultsElement.replaceChildren();
        statusElement.textContent = message;
    }

    function renderResults(posts, keyword) {
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
            search.cancel();
            pendingRequest?.abort();
            requestVersion += 1;
            clearResults();
            return;
        }
        search(keyword);
    }).on("keydown", function (event) {
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
