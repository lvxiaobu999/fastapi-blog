/**
 * 登录状态与认证表单入口。
 *
 * 页面启动后，本模块会验证缓存 Token；登录、注册、退出都在这里绑定事件。
 * api.js 发现身份失效时只派发事件，由本模块负责展示登录模态框，避免其他业务
 * 模块分别依赖 Bootstrap 弹窗细节。
 */

import {
    ajaxRequest, clearToken, errorMessages, getToken, requireLogin, saveToken,
    setAdminState, setAuthState,
} from "./api.js";
import {setButtonLoading} from "./ui.js";

function showFeedback($form, message, kind = "danger") {
    // 同一个反馈区域可在成功/失败样式间复用，先清旧样式再写新内容。
    $form.find("[data-form-feedback]")
        .removeClass("d-none alert-danger alert-success")
        .addClass(`alert-${kind}`)
        .text(message);
}

function openLoginModal(message) {
    // layout 页面有模态框，独立登录页面只有普通表单，因此依次尝试两种容器。
    const form = document.querySelector("#loginModal [data-login-form]")
        ?? document.querySelector("[data-login-form]");
    if (form && message) showFeedback($(form), message);
    const modal = document.querySelector("#loginModal");
    if (modal) bootstrap.Modal.getOrCreateInstance(modal).show();
}

// api.js 在受保护请求无法刷新会话时统一触发该事件，各业务模块无需重复操作模态框。
document.addEventListener("blog:auth-required", (event) => {
    openLoginModal(event.detail?.message ?? "请重新登录后继续操作。");
});

$(function () {
    // jQuery ready：DOM 可查询后再绑定事件和校验当前用户。
    const adminContent = document.querySelector("[data-require-admin]");
    const adminDenied = document.querySelector("[data-admin-denied]");

    function applyCurrentUser(user) {
        // /auth/me 才是可信状态来源；缓存 Token 只用于避免首屏导航闪烁。
        setAuthState(true);
        setAdminState(user.is_admin);
        // 用户菜单由 /auth/me 的可信响应填充，不能从 localStorage 猜测昵称或头像。
        document.querySelectorAll("[data-current-user-avatar]").forEach((image) => {
            image.src = user.image_path;
            image.alt = `${user.nickname}的头像`;
        });
        document.querySelectorAll("[data-current-user-nickname]")
            .forEach((element) => { element.textContent = user.nickname; });
        document.querySelectorAll("[data-current-user-handle]")
            .forEach((element) => { element.textContent = `@${user.username}`; });
        document.querySelectorAll("[data-profile-link]")
            .forEach((link) => { link.href = `/profile/${user.id}`; });
        if (!adminContent) return;
        adminContent.hidden = !user.is_admin;
        if (adminDenied) adminDenied.hidden = user.is_admin;
    }

    $("[data-login-form]").on("submit", function (event) {
        // 拦截原生表单跳转，改用 OAuth2 表单编码请求获取 Access Token。
        event.preventDefault();
        const $form = $(this);
        const button = $form.find("[type=submit]")[0];
        if (!setButtonLoading(button, true, "登录中…")) return;
        ajaxRequest({
            url: "/api/auth/token",
            method: "POST",
            formEncoded: true,
            data: $form.serialize(),
        }).done((result) => {
            // 保存 Token 后刷新整页，使所有依赖登录身份的页面模块重新初始化。
            saveToken(result.access_token);
            showFeedback($form, "登录成功，正在刷新页面……", "success");
            window.setTimeout(() => window.location.reload(), 350);
        }).fail((xhr) => {
            setButtonLoading(button, false);
            showFeedback($form, errorMessages(xhr));
        });
    });

    $("[data-register-form]").on("submit", function (event) {
        // 确认密码只在前端比较，不发送给后端；真正密码规则仍由 Schema 校验。
        event.preventDefault();
        const $form = $(this);
        const password = $form.find("[name=password]").val();
        if (password !== $form.find("[name=password_confirm]").val()) {
            showFeedback($form, "两次输入的密码不一致。");
            return;
        }
        const button = $form.find("[type=submit]")[0];
        if (!setButtonLoading(button, true, "注册中…")) return;
        ajaxRequest({
            url: "/api/users",
            method: "POST",
            data: {
                username: $form.find("[name=username]").val(),
                email: $form.find("[name=email]").val(),
                password,
            },
        }).done(() => {
            showFeedback($form, "注册成功，请使用新账号登录。", "success");
            $form[0].reset();
        }).fail((xhr) => showFeedback($form, errorMessages(xhr)))
            .always(() => setButtonLoading(button, false));
    });

    $("[data-logout]").on("click", function () {
        // 后端负责撤销 Refresh 会话；无论请求结果如何，本机都应清除 Access Token。
        const button = this;
        if (!setButtonLoading(button, true, "退出中…")) return;
        $.ajax({url: "/api/auth/logout", method: "POST"}).always(() => {
            clearToken();
            window.location.reload();
        });
    });

    $("[data-password-form]").on("submit", function (event) {
        event.preventDefault();
        const $form = $(this);
        const newPassword = $form.find("[name=new_password]").val();
        if (newPassword !== $form.find("[name=confirm_password]").val()) {
            showFeedback($form, "两次输入的新密码不一致。");
            return;
        }
        const button = $form.find("[type=submit]")[0];
        if (!setButtonLoading(button, true, "修改中…")) return;
        ajaxRequest({
            url: "/api/auth/password",
            method: "POST",
            auth: true,
            data: {
                current_password: $form.find("[name=current_password]").val(),
                new_password: newPassword,
                confirm_password: $form.find("[name=confirm_password]").val(),
            },
        }).done(() => {
            // 后端已经撤销该用户全部 Redis Refresh Session。无状态 Access JWT 无法由
            // Redis 立即撤销，因此前端同步删除本地 Access Token，并刷新成游客状态。
            clearToken();
            setAuthState(false);
            showFeedback($form, "密码修改成功，请重新登录。", "success");
            $form[0].reset();
            window.setTimeout(() => window.location.reload(), 600);
        }).fail((xhr) => showFeedback($form, errorMessages(xhr)))
            .always(() => setButtonLoading(button, false));
    });

    // head 中已经根据缓存 Token 同步设置首屏状态；这里再向后端验证真实性。
    // ajaxRequest 会在 Access Token 过期时尝试 Refresh，最终失败则清理状态并弹登录框。
    if (getToken()) {
        ajaxRequest({url: "/api/auth/me", auth: true, refreshAuth: false})
            .done(applyCurrentUser);
    } else {
        setAuthState(false);
        if (adminContent) requireLogin("请先登录管理员账号后再发布文章。");
    }
});
