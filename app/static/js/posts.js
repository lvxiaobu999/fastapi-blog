/** 帖子写入表单；公共 AJAX 封装会自动附加当前会话的 Bearer Token。 */

import {ajaxRequest, errorMessages, uploadFile} from "./api.js";

function decorateCodeBlocks(root) {
    root.querySelectorAll("pre").forEach((pre) => {
        const code = pre.querySelector("code");
        if (!code) {
            return;
        }
        const languageClass = [...pre.classList, ...code.classList].find((name) =>
            /^(?:lang|language)-/.test(name),
        );
        const language = languageClass
            ? languageClass.replace(/^(?:lang|language)-/, "")
            : "text";
        pre.dataset.language = language.toUpperCase();
        pre.classList.add("rich-code-block");
    });
}

$(function () {
    const viewerElement = document.querySelector("#post-viewer");
    const viewerSource = document.querySelector("#post-markdown");
    if (viewerElement && viewerSource) {
        const initializeViewer = () => {
            if (!window.toastui?.Editor?.factory) {
                return false;
            }
            const codeSyntaxHighlight = window.toastui.Editor.plugin?.codeSyntaxHighlight;
            window.toastui.Editor.factory({
                el: viewerElement,
                viewer: true,
                initialValue: viewerSource.value,
                plugins: codeSyntaxHighlight ? [codeSyntaxHighlight] : [],
            });
            // Viewer 内容是静态的，渲染完成后装饰一次即可；持续监听 Toast UI 的 DOM
            // 会在插入代码块时触发大量重复扫描，导致编辑界面卡顿。
            window.setTimeout(() => decorateCodeBlocks(viewerElement), 0);
            return true;
        };

        // 模块脚本和 CDN 脚本的执行顺序在不同浏览器/缓存状态下可能不同，短暂重试
        // 可以避免 Toast UI 尚未挂载到 window 时直接进入降级显示。
        if (!initializeViewer()) {
            window.setTimeout(initializeViewer, 100);
            window.setTimeout(() => {
                if (initializeViewer()) {
                    return;
                }
                // CDN 不可用时不要让正文区域看起来像“没有内容”；同时不把 Markdown 当 HTML 注入。
                viewerElement.textContent = viewerSource.value;
                viewerElement.classList.add("post-viewer-fallback");
            }, 800);
        }
    }

    let editor = null;
    const editorElement = document.querySelector("#post-editor");
    if (editorElement && window.toastui?.Editor) {
        const source = document.querySelector("#content");
        const codeSyntaxHighlight = window.toastui.Editor.plugin?.codeSyntaxHighlight;
        editor = new window.toastui.Editor({
            el: editorElement,
            height: "520px",
            initialEditType: "wysiwyg",
            previewStyle: "vertical",
            initialValue: source.value,
            plugins: codeSyntaxHighlight ? [codeSyntaxHighlight] : [],
            hooks: {
                addImageBlobHook(blob, callback) {
                    uploadFile("/api/posts/images", "image", blob)
                        .done((result) => callback(result.url, blob.name || "文章图片"))
                        .fail((xhr) => window.alert(errorMessages(xhr)));
                },
            },
        });
    }

    $("[data-post-form]").on("submit", function (event) {
        event.preventDefault();
        const $form = $(this);
        const postId = $form.data("post-id");
        ajaxRequest({
            url: postId ? `/api/posts/${postId}` : "/api/posts",
            method: postId ? "PATCH" : "POST",
            auth: true,
            data: {
                title: $form.find("[name=title]").val(),
                content: editor ? editor.getMarkdown() : $form.find("[name=content]").val(),
            },
        }).done((post) => {
            window.location.assign(`/posts/${post.id}`);
        }).fail((xhr) => {
            $form.find("[data-form-feedback]")
                .removeClass("d-none")
                .text(errorMessages(xhr));
        });
    });
});
