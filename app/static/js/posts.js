/**
 * 帖子详情渲染、编辑器、预览和发布入口。
 *
 * 同一脚本会加载到帖子详情页和编辑页，因此每一段都先检查目标 DOM 是否存在：
 * 详情页初始化只读 Viewer；编辑页初始化 Editor、图片上传、预览与提交逻辑。
 */

import {ajaxRequest, errorMessages, uploadFile} from "./api.js";
import {setButtonLoading} from "./ui.js";

function decorateCodeBlocks(root) {
    // Toast UI 已经完成 Markdown 渲染；这里只读取语言 class 并添加视觉标签。
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
    // ---------- 详情页：把隐藏 textarea 中的 Markdown 渲染为只读正文 ----------
    const viewerElement = document.querySelector("#post-viewer");
    const viewerSource = document.querySelector("#post-markdown");
    if (viewerElement && viewerSource) {
        const initializeViewer = () => {
            // CDN 脚本可能尚未加载完成，用返回值告诉外层是否需要稍后重试。
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
    let previewViewer = null;
    // ---------- 编辑页：创建 Toast UI Editor，并把图片交给后端上传 ----------
    const editorElement = document.querySelector("#post-editor");
    if (editorElement && window.toastui?.Editor) {
        const source = document.querySelector("#content");
        const codeSyntaxHighlight = window.toastui.Editor.plugin?.codeSyntaxHighlight;
        editor = new window.toastui.Editor({
            el: editorElement,
            height: "520px",
            initialEditType: "wysiwyg",
            // 桌面端并排查看 Markdown 与效果；窄屏使用标签切换，避免两个面板撑破容器。
            previewStyle: window.matchMedia("(max-width: 767.98px)").matches
                ? "tab"
                : "vertical",
            initialValue: source.value,
            plugins: codeSyntaxHighlight ? [codeSyntaxHighlight] : [],
            hooks: {
                addImageBlobHook(blob, callback) {
                    // 上传成功后 callback 把服务端 URL 插回 Markdown；失败不插入占位图。
                    uploadFile("/api/posts/images", "image", blob)
                        .done((result) => callback(result.url, blob.name || "文章图片"))
                        .fail((xhr) => window.alert(errorMessages(xhr)));
                },
            },
        });
    }

    $("[data-post-preview]").on("click", function () {
        // ---------- 预览：读取编辑器当前值，不保存、不调用帖子写入 API ----------
        const title = $("[data-post-form] [name=title]").val().trim() || "未命名文章";
        const markdown = editor
            ? editor.getMarkdown()
            : $("[data-post-form] [name=content]").val();
        const previewElement = document.querySelector("#post-preview-viewer");
        const emptyElement = document.querySelector("[data-preview-empty]");

        $("[data-preview-title]").text(title);
        emptyElement.classList.toggle("d-none", Boolean(markdown.trim()));
        previewElement.classList.toggle("d-none", !markdown.trim());

        if (!markdown.trim()) {
            return;
        }

        if (!window.toastui?.Editor?.factory) {
            // CDN 暂时不可用时仍展示原始 Markdown，避免用户点击预览后看到空白区域。
            previewElement.textContent = markdown;
            previewElement.classList.add("post-viewer-fallback");
            return;
        }
        previewElement.classList.remove("post-viewer-fallback");

        if (previewViewer) {
            // Viewer 可以直接替换 Markdown，无需反复销毁并创建 DOM，连续预览时更流畅。
            previewViewer.setMarkdown(markdown);
        } else {
            const codeSyntaxHighlight = window.toastui.Editor.plugin?.codeSyntaxHighlight;
            previewViewer = window.toastui.Editor.factory({
                el: previewElement,
                viewer: true,
                initialValue: markdown,
                plugins: codeSyntaxHighlight ? [codeSyntaxHighlight] : [],
            });
        }

        // 预览与详情页共用代码块标题装饰，确保语言标签和深色代码区域一致。
        window.setTimeout(() => decorateCodeBlocks(previewElement), 0);
    });

    $("[data-post-form]").on("submit", function (event) {
        // ---------- 保存：是否存在 postId 决定创建 POST 还是更新 PATCH ----------
        event.preventDefault();
        const $form = $(this);
        const postId = $form.data("post-id");
        const button = $form.find("[type=submit]")[0];
        if (!setButtonLoading(button, true, postId ? "保存中…" : "发布中…")) return;
        ajaxRequest({
            url: postId ? `/api/posts/${postId}` : "/api/posts",
            method: postId ? "PATCH" : "POST",
            auth: true,
            data: {
                title: $form.find("[name=title]").val(),
                category_id: Number($form.find("[name=category_id]").val()),
                content: editor ? editor.getMarkdown() : $form.find("[name=content]").val(),
            },
        }).done((post) => {
            // 服务端返回最终 ID 后进入详情页；页面重载会初始化只读 Viewer。
            window.location.assign(`/posts/${post.id}`);
        }).fail((xhr) => {
            // 失败时必须解除按钮锁，让用户修改内容后可以再次提交。
            setButtonLoading(button, false);
            $form.find("[data-form-feedback]")
                .removeClass("d-none")
                .text(errorMessages(xhr));
        });
    });
});
