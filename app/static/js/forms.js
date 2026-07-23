/** 搜索与个人资料表单 AJAX 行为；页面中的表单不再触发浏览器原生提交。 */

import {ajaxRequest, errorMessages} from "./api.js";

$(document).on("submit", "[data-search-form]", function (event) {
    event.preventDefault();
    const url = `${this.action}?${$(this).serialize()}`;
    $.ajax({url, method: "GET", dataType: "html"})
        .done((html) => {
            const nextDocument = new DOMParser().parseFromString(html, "text/html");
            $("main.page-content").html($(nextDocument).find("main.page-content").html());
            window.history.pushState({}, "", url);
        })
        .fail(() => window.alert("搜索失败，请稍后重试。"));
});

$(document).on("submit", "[data-profile-form]", function (event) {
    event.preventDefault();
    const $form = $(this);
    if ($form.find("[name=avatar]")[0].files.length) {
        $form.find("[data-form-feedback]")
            .removeClass("d-none alert-success")
            .addClass("alert-danger")
            .text("头像上传接口尚未实现，请先清空头像文件后保存其他资料。");
        return;
    }
    ajaxRequest({
        url: `/api/users/${$form.data("user-id")}`,
        method: "PATCH",
        auth: true,
        data: {
            nickname: $form.find("[name=nickname]").val(),
            username: $form.find("[name=username]").val(),
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
    });
});
