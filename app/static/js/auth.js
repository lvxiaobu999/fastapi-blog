/** 登录、注册模态框交互；所有提交都复用公共 AJAX 模块。 */

import {ajaxRequest, clearToken, errorMessages, getToken, saveToken} from "./api.js";

function showFeedback($form, message, kind = "danger") {
    $form.find("[data-form-feedback]")
        .removeClass("d-none alert-danger alert-success")
        .addClass(`alert-${kind}`)
        .text(message);
}

$(function () {
    $("[data-login-form]").on("submit", function (event) {
        event.preventDefault();
        const $form = $(this);
        ajaxRequest({
            url: "/api/auth/token",
            method: "POST",
            formEncoded: true,
            data: $form.serialize(),
        }).done((result) => {
            saveToken(result.access_token);
            showFeedback($form, "登录成功，正在刷新页面……", "success");
            window.setTimeout(() => window.location.reload(), 350);
        }).fail((xhr) => showFeedback($form, errorMessages(xhr)));
    });

    $("[data-register-form]").on("submit", function (event) {
        event.preventDefault();
        const $form = $(this);
        const password = $form.find("[name=password]").val();
        if (password !== $form.find("[name=password_confirm]").val()) {
            showFeedback($form, "两次输入的密码不一致。");
            return;
        }
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
        }).fail((xhr) => showFeedback($form, errorMessages(xhr)));
    });

    const loggedIn = Boolean(getToken());
    $("[data-auth-guest]").toggleClass("d-none", loggedIn);
    $("[data-auth-user]").toggleClass("d-none", !loggedIn);
    $("[data-logout]").on("click", () => {
        clearToken();
        window.location.reload();
    });
});
