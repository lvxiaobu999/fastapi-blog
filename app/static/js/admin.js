/**
 * 管理后台页面入口。
 *
 * 后台 HTML 只是界面外壳。启动时必须调用 /api/auth/me 验证管理员身份，之后用户
 * 写操作走 /api/admin/users，帖子写操作复用已经受 AdminUser 保护的 /api/posts。
 */

import {ajaxRequest, errorMessages} from "./api.js";
import {setButtonLoading} from "./ui.js";

$(function () {
    const page = document.body.dataset.adminPage;
    const content = document.querySelector("[data-admin-content]");
    const accessState = document.querySelector("[data-admin-state]");
    const deleteModalElement = document.querySelector("[data-delete-modal]");
    const deleteModal = deleteModalElement
        ? bootstrap.Modal.getOrCreateInstance(deleteModalElement)
        : null;
    let pendingDelete = null;

    function showAccessError(message) {
        accessState.textContent = message;
        content.hidden = true;
    }

    function formatDate(value) {
        return new Intl.DateTimeFormat("zh-CN", {dateStyle: "medium"}).format(new Date(value));
    }

    function actionButton(label, className, action, id) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = className;
        button.textContent = label;
        button.dataset.action = action;
        button.dataset.id = id;
        return button;
    }

    function loadUsers() {
        const rows = document.querySelector("[data-user-rows]");
        if (!rows) return;
        ajaxRequest({url: "/api/admin/users", auth: true}).done((users) => {
            rows.replaceChildren();
            users.forEach((user) => {
                const row = document.createElement("tr");
                const identity = document.createElement("td");
                identity.innerHTML = `<div class="admin-user-cell"><img alt=""><div><strong></strong><span></span></div></div>`;
                identity.querySelector("img").src = user.image_path;
                identity.querySelector("strong").textContent = user.nickname;
                identity.querySelector("span").textContent = `@${user.username}`;
                const email = document.createElement("td"); email.textContent = user.email;
                const role = document.createElement("td"); role.textContent = user.is_admin ? "管理员" : "普通用户";
                const actions = document.createElement("td"); actions.className = "admin-row-actions";
                const deleteUser = actionButton("删除", "btn btn-sm btn-outline-danger", "delete-user", user.id);
                actions.append(
                    actionButton("编辑", "btn btn-sm btn-outline-secondary", "edit-user", user.id),
                    deleteUser,
                );
                row.append(identity, email, role, actions);
                row.dataset.user = JSON.stringify(user);
                rows.append(row);
            });
            document.querySelector("[data-user-empty]").classList.toggle("d-none", users.length > 0);
        });
    }

    function loadPosts() {
        const rows = document.querySelector("[data-post-rows]");
        if (!rows) return;
        // 管理员入口包含下架文章，公开列表接口会主动过滤它们。
        ajaxRequest({url: "/api/posts/admin?limit=100", auth: true}).done((posts) => {
            rows.replaceChildren();
            posts.forEach((post) => {
                const row = document.createElement("tr");
                const title = document.createElement("td");
                const link = document.createElement("a"); link.href = `/posts/${post.id}`; link.textContent = post.title;
                title.append(link);
                const category = document.createElement("td");
                category.textContent = post.categories.map((item) => item.name).join("、");
                const author = document.createElement("td"); author.textContent = post.author.nickname;
                const status = document.createElement("td");
                const badge = document.createElement("span");
                badge.className = `status-pill ${post.is_published ? "status-published" : "status-unpublished"}`;
                badge.textContent = post.is_published ? "已上架" : "已下架";
                status.append(badge);
                const date = document.createElement("td"); date.textContent = formatDate(post.created_at);
                const actions = document.createElement("td"); actions.className = "admin-row-actions";
                const edit = document.createElement("a"); edit.className = "btn btn-sm btn-outline-secondary"; edit.href = `/posts/${post.id}/edit`; edit.textContent = "编辑";
                const publish = actionButton(post.is_published ? "下架" : "上架", "btn btn-sm btn-outline-secondary", "toggle-post", post.id);
                publish.dataset.published = String(post.is_published);
                const deletePost = actionButton("删除", "btn btn-sm btn-outline-danger", "delete-post", post.id);
                actions.append(edit, publish, deletePost);
                row.append(title, category, author, status, date, actions); rows.append(row);
            });
            document.querySelector("[data-post-empty]").classList.toggle("d-none", posts.length > 0);
        });
    }

    function initializePage() {
        document.querySelector(`[data-admin-nav="${page}"]`)?.classList.add("active");
        if (page === "users") loadUsers();
        if (page === "posts") loadPosts();
        if (page === "dashboard") {
            ajaxRequest({url: "/api/admin/users", auth: true}).done((users) => { document.querySelector("[data-user-total]").textContent = users.length; });
            ajaxRequest({url: "/api/posts/admin?limit=100", auth: true}).done((posts) => { document.querySelector("[data-post-total]").textContent = posts.length; });
        }
    }

    ajaxRequest({url: "/api/auth/me", auth: true}).done((user) => {
        if (!user.is_admin) {
            // HTML GET 无法读取 localStorage Token，所以先通过 API 确认身份。普通用户身份
            // 有效但权限不足，跳到真正返回 HTTP 403 的页面；replace 防止返回键反复进入。
            window.location.replace("/forbidden");
            return;
        }
        document.querySelector("[data-admin-identity]").textContent = user.nickname;
        document.querySelector("[data-admin-avatar]").src = user.image_path;
        accessState.hidden = true;
        content.hidden = false;
        initializePage();
        // 发布页最初隐藏正文，等权限确认后再通知富文本编辑器计算可见容器的尺寸。
        document.dispatchEvent(new CustomEvent("blog:admin-ready"));
    }).fail(() => {
        // ajaxRequest 遇到 401 时已经清理失效 Token，并通过 blog:auth-required
        // 通知 auth.js 打开登录模态框。后台这里只隐藏受保护内容，避免再显示一套
        // “返回博客登录”的提示；登录成功后 auth.js 会刷新本页并重新校验管理员权限。
        content.hidden = true;
    });

    $("[data-admin-menu]").on("click", () => document.querySelector(".admin-sidebar").classList.toggle("open"));
    $("[data-user-create]").on("click", () => {
        const form = document.querySelector("[data-admin-user-form]"); form.reset(); form.user_id.value = "";
        form.querySelector("[data-password-field]").hidden = false;
        document.querySelector("[data-user-modal-title]").textContent = "新增用户";
        bootstrap.Modal.getOrCreateInstance(document.querySelector("#adminUserModal")).show();
    });

    $(document).on("click", "[data-action=edit-user]", function () {
        const user = JSON.parse(this.closest("tr").dataset.user); const form = document.querySelector("[data-admin-user-form]");
        form.user_id.value = user.id; form.username.value = user.username; form.nickname.value = user.nickname;
        form.email.value = user.email; form.is_admin.checked = user.is_admin; form.querySelector("[data-password-field]").hidden = true;
        document.querySelector("[data-user-modal-title]").textContent = "编辑用户";
        bootstrap.Modal.getOrCreateInstance(document.querySelector("#adminUserModal")).show();
    });

    $("[data-admin-user-form]").on("submit", function (event) {
        event.preventDefault(); const id = this.user_id.value; const button = this.querySelector("[type=submit]");
        if (!setButtonLoading(button, true, "保存中…")) return;
        const data = {username: this.username.value, nickname: this.nickname.value || null, email: this.email.value, is_admin: this.is_admin.checked};
        if (!id) data.password = this.password.value;
        ajaxRequest({url: id ? `/api/admin/users/${id}` : "/api/admin/users", method: id ? "PATCH" : "POST", auth: true, data})
            .done(() => { bootstrap.Modal.getInstance(document.querySelector("#adminUserModal")).hide(); loadUsers(); })
            .fail((xhr) => { const feedback = this.querySelector("[data-form-feedback]"); feedback.className = "alert alert-danger"; feedback.textContent = errorMessages(xhr); })
            .always(() => setButtonLoading(button, false));
    });

    $(document).on("click", "[data-action=delete-user], [data-action=delete-post]", function () {
        const isUser = this.dataset.action === "delete-user";
        pendingDelete = {
            id: this.dataset.id,
            isUser,
            sourceButton: this,
        };
        document.querySelector("[data-delete-title]").textContent = `确认删除${isUser ? "用户" : "帖子"}？`;
        document.querySelector("[data-delete-message]").textContent = isUser
            ? "用户账号及其关联内容将被永久删除，此操作无法撤销。"
            : "帖子正文、评论以及相关互动数据将被永久删除。暂时隐藏内容请使用“下架”。";
        document.querySelector("[data-delete-feedback]").classList.add("d-none");
        deleteModal?.show();
    });

    $(document).on("click", "[data-delete-confirm]", function () {
        if (!pendingDelete || !setButtonLoading(this, true, "删除中…")) return;
        const target = pendingDelete;
        ajaxRequest({
            url: target.isUser ? `/api/admin/users/${target.id}` : `/api/posts/${target.id}`,
            method: "DELETE",
            auth: true,
        })
            .done(() => {
                deleteModal?.hide();
                target.isUser ? loadUsers() : loadPosts();
            })
            .fail((xhr) => {
                const feedback = document.querySelector("[data-delete-feedback]");
                feedback.textContent = errorMessages(xhr);
                feedback.classList.remove("d-none");
            })
            .always(() => setButtonLoading(this, false));
    });

    deleteModalElement?.addEventListener("hidden.bs.modal", () => {
        pendingDelete = null;
        document.querySelector("[data-delete-feedback]").classList.add("d-none");
    });

    $(document).on("click", "[data-action=toggle-post]", function () {
        const nextPublished = this.dataset.published !== "true";
        if (!setButtonLoading(this, true, nextPublished ? "上架中…" : "下架中…")) return;
        ajaxRequest({url: `/api/posts/${this.dataset.id}`, method: "PATCH", auth: true, data: {is_published: nextPublished}})
            .done(loadPosts)
            .fail((xhr) => { window.alert(errorMessages(xhr)); setButtonLoading(this, false); });
    });
});
