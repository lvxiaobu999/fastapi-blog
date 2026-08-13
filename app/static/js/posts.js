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
            // 站内第三方脚本可能尚未执行完成，用返回值告诉外层是否需要稍后重试。
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

        // 模块脚本和普通 vendor 脚本的执行顺序在不同浏览器/缓存状态下可能不同，短暂重试
        // 可以避免 Toast UI 尚未挂载到 window 时直接进入降级显示。
        if (!initializeViewer()) {
            window.setTimeout(initializeViewer, 100);
            window.setTimeout(() => {
                if (initializeViewer()) {
                    return;
                }
                // vendor 脚本加载失败时仍显示 Markdown 原文，且不把 Markdown 当 HTML 注入。
                viewerElement.textContent = viewerSource.value;
                viewerElement.classList.add("post-viewer-fallback");
            }, 800);
        }
    }

    let editor = null;
    let editorInitializationStarted = false;
    let editorInitializationFailed = false;
    let previewViewer = null;
    let postLoaded = true;
    const postForm = document.querySelector("[data-post-form]");
    const editingPostId = postForm?.dataset.postId;
    const coverInput = document.querySelector("#cover-image");
    const coverUrlInput = document.querySelector("#cover_image_url");
    const coverPreview = document.querySelector("[data-cover-preview]");
    const coverPlaceholder = document.querySelector("[data-cover-placeholder]");
    const coverRemoveButton = document.querySelector("[data-cover-remove]");
    const coverFeedback = document.querySelector("[data-cover-feedback]");

    function selectedCategoryIds() {
        return [...postForm.querySelectorAll("[name=category_ids]:checked")]
            .map((input) => Number(input.value));
    }

    function showCategories(categoryIds) {
        const selectedIds = new Set(categoryIds.map(String));
        postForm.querySelectorAll("[name=category_ids]").forEach((input) => {
            input.checked = selectedIds.has(input.value);
        });
    }

    function showCover(url) {
        // 隐藏字段保存上传接口返回的站内 URL，提交文章时再与其他字段一起写入数据库。
        coverUrlInput.value = url || "";
        coverPreview.src = url || "";
        coverPreview.hidden = !url;
        coverPlaceholder.hidden = Boolean(url);
        coverRemoveButton.classList.toggle("d-none", !url);
    }

    coverInput?.addEventListener("change", function () {
        const file = this.files?.[0];
        if (!file) return;
        this.disabled = true;
        coverFeedback.textContent = "横图上传中…";
        uploadFile("/api/posts/images", "image", file)
            .done((result) => {
                showCover(result.url);
                coverFeedback.textContent = "横图已上传，保存文章后生效。";
            })
            .fail((xhr) => {
                coverFeedback.textContent = errorMessages(xhr);
                this.value = "";
            })
            .always(() => { this.disabled = false; });
    });

    coverRemoveButton?.addEventListener("click", () => {
        showCover("");
        coverInput.value = "";
        coverFeedback.textContent = "横图将在保存文章后移除。";
    });
    // ---------- 编辑页：在管理员内容可见后创建 Editor，并把图片交给后端上传 ----------
    const editorElement = document.querySelector("#post-editor");
    const editorSource = document.querySelector("#content");
    const editorFeedback = document.querySelector("[data-editor-feedback]");

    function showEditorFallback() {
        if (!editorElement || !editorSource || editorInitializationFailed) {
            return;
        }
        editorInitializationFailed = true;
        editorElement.replaceChildren();
        editorElement.classList.add("d-none");
        editorSource.classList.remove("d-none");
        editorSource.classList.add("form-control", "post-editor-fallback");
        editorSource.rows = 18;
        editorSource.required = true;
        if (editorFeedback) {
            editorFeedback.textContent = "富文本编辑器加载失败，已切换为普通文本框，文章仍可正常保存。";
            editorFeedback.classList.remove("d-none");
        }
    }

    function initializeEditor() {
        if (editor) {
            return "ready";
        }
        if (editorInitializationFailed) {
            return "failed";
        }
        if (!window.toastui?.Editor) {
            return "waiting";
        }

        try {
            const codeSyntaxHighlight = window.toastui.Editor.plugin?.codeSyntaxHighlight;
            editor = new window.toastui.Editor({
                el: editorElement,
                height: "520px",
                initialEditType: "wysiwyg",
                // 桌面端并排查看 Markdown 与效果；窄屏使用标签切换，避免两个面板撑破容器。
                previewStyle: window.matchMedia("(max-width: 767.98px)").matches
                    ? "tab"
                    : "vertical",
                initialValue: editorSource.value,
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
            return "ready";
        } catch (error) {
            // 第三方构建损坏时不能只留下空白区域；记录错误并保留普通文本编辑能力。
            console.error("Toast UI Editor 初始化失败。", error);
            showEditorFallback();
            return "failed";
        }
    }

    function startEditorInitialization() {
        if (!editorElement || !editorSource || editorInitializationStarted) {
            return;
        }
        const adminContent = editorElement.closest("[data-admin-content]");
        if (adminContent?.hidden) {
            return;
        }
        editorInitializationStarted = true;

        if (initializeEditor() !== "waiting") {
            return;
        }
        // 普通 script 与 module script 在缓存状态不同的浏览器中可能出现短暂时序差异。
        window.setTimeout(initializeEditor, 100);
        window.setTimeout(() => {
            if (initializeEditor() === "waiting") {
                showEditorFallback();
            }
        }, 800);
    }

    if (editorElement) {
        // admin.js 校验管理员后才显示 main；隐藏时初始化会让编辑器错误计算宽高。
        document.addEventListener("blog:admin-ready", startEditorInitialization, {once: true});
        startEditorInitialization();
    }

    if (editingPostId) {
        // 编辑页 HTML 不能读取 localStorage 中的 Bearer Token，因此服务端只渲染空表单。
        // 在管理员 API 验证身份后再填充草稿，避免匿名 GET 泄露未发布内容。
        postLoaded = false;
        const submitButton = postForm.querySelector("[type=submit]");
        submitButton.disabled = true;
        ajaxRequest({url: `/api/posts/admin/${editingPostId}`, auth: true})
            .done((post) => {
                postForm.elements.title.value = post.title;
                showCategories(post.category_ids);
                postForm.elements.summary.value = post.summary || "";
                postForm.elements.is_published.checked = post.is_published;
                editorSource.value = post.content;
                editor?.setMarkdown(post.content);
                showCover(post.cover_image_url || "");
                postLoaded = true;
                submitButton.disabled = false;
            })
            .fail((xhr) => {
                postForm.querySelector("[data-form-feedback]")
                    .classList.remove("d-none");
                postForm.querySelector("[data-form-feedback]").textContent = errorMessages(xhr);
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
        const summary = $("[data-post-form] [name=summary]").val().trim();
        const coverUrl = coverUrlInput?.value || "";
        const previewSummary = document.querySelector("[data-preview-summary]");
        const previewCover = document.querySelector("[data-preview-cover]");
        previewSummary.textContent = summary;
        previewSummary.classList.toggle("d-none", !summary);
        previewCover.src = coverUrl;
        previewCover.classList.toggle("d-none", !coverUrl);
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
        if (!postLoaded) return;
        if (!setButtonLoading(button, true, postId ? "保存中…" : "发布中…")) return;
        ajaxRequest({
            url: postId ? `/api/posts/${postId}` : "/api/posts",
            method: postId ? "PATCH" : "POST",
            auth: true,
            data: {
                title: $form.find("[name=title]").val(),
                category_ids: selectedCategoryIds(),
                summary: $form.find("[name=summary]").val().trim() || null,
                cover_image_url: coverUrlInput?.value || null,
                content: editor ? editor.getMarkdown() : $form.find("[name=content]").val(),
                is_published: $form.find("[name=is_published]").prop("checked"),
            },
        }).done((post) => {
            // 服务端返回最终 ID 后进入详情页；页面重载会初始化只读 Viewer。
            window.location.assign(post.is_published ? `/posts/${post.id}` : "/admin/posts");
        }).fail((xhr) => {
            // 失败时必须解除按钮锁，让用户修改内容后可以再次提交。
            setButtonLoading(button, false);
            $form.find("[data-form-feedback]")
                .removeClass("d-none")
                .text(errorMessages(xhr));
        });
    });
});
