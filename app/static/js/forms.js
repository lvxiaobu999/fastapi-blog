/**
 * 普通页面表单行为。
 *
 * 搜索表单请求服务端渲染后的 HTML 并替换 main；资料表单调用 JSON API。
 * 使用 document 委托监听，是为了搜索结果替换 main 后，新插入的表单仍能响应。
 */

import {ajaxRequest, errorMessages, uploadFile} from "./api.js";
import {setButtonLoading} from "./ui.js";

$(document).on("submit", "[data-search-form]", function (event) {
    // 阻止浏览器整页提交，但仍把表单字段序列化为标准查询字符串。
    event.preventDefault();
    const button = this.querySelector("[type=submit]");
    if (!setButtonLoading(button, true, "搜索中…")) return;
    const url = `${this.action}?${$(this).serialize()}`;
    $.ajax({url, method: "GET", dataType: "html"})
        .done((html) => {
            // 只替换主内容区，导航栏和全局模态框无需重新加载。
            const nextDocument = new DOMParser().parseFromString(html, "text/html");
            $("main.page-content").html($(nextDocument).find("main.page-content").html());
            window.history.pushState({}, "", url);
        })
        .fail(() => window.alert("搜索失败，请稍后重试。"))
        .always(() => setButtonLoading(button, false));
});

$(document).on("submit", "[data-profile-form]", function (event) {
    event.preventDefault();
    const $form = $(this);
    const button = $form.find("[type=submit]")[0];
    if (!setButtonLoading(button, true, "保存中…")) return;
    ajaxRequest({
        url: `/api/users/${$form.data("user-id")}`,
        method: "PATCH",
        auth: true,
        data: {
            nickname: $form.find("[name=nickname]").val(),
            email: $form.find("[name=email]").val(),
        },
    }).done(() => {
        $form.find("[data-form-feedback]")
            .removeClass("d-none alert-danger")
            .addClass("alert-success")
            .text("个人资料已保存。");
    }).fail((xhr) => {
        $form.find("[data-form-feedback]")
            .removeClass("d-none alert-success")
            .addClass("alert-danger")
            .text(errorMessages(xhr));
    }).always(() => setButtonLoading(button, false));
});

$(document).on("change", "[data-avatar-input]", function () {
    // 头像是独立动作：用户选中文件后立即上传，不依赖资料表单的保存按钮。
    const input = this;
    const file = input.files?.[0];
    if (!file) return;
    const editor = input.closest("[data-avatar-editor]");
    const feedback = document.querySelector("[data-avatar-feedback]");
    const actionText = editor.querySelector("[data-avatar-action-text]");
    if (editor.getAttribute("aria-busy") === "true") return;

    editor.setAttribute("aria-busy", "true");
    input.disabled = true;
    actionText.textContent = "上传中…";
    feedback.classList.remove("is-error", "is-success");
    feedback.textContent = "正在上传头像…";

    uploadFile("/api/users/me/avatar", "avatar", file)
        .done((user) => {
            // 接口返回的新地址是唯一可信结果，同时更新资料页和导航中的当前用户头像。
            document.querySelectorAll("[data-profile-avatar-image], [data-current-user-avatar]")
                .forEach((image) => {
                    image.src = user.image_path;
                    image.alt = `${user.nickname}的头像`;
                });
            feedback.classList.add("is-success");
            feedback.textContent = "头像已更新";
        })
        .fail((xhr) => {
            feedback.classList.add("is-error");
            feedback.textContent = errorMessages(xhr);
        })
        .always(() => {
            editor.removeAttribute("aria-busy");
            input.disabled = false;
            input.value = "";
            actionText.textContent = "修改头像";
        });
});
