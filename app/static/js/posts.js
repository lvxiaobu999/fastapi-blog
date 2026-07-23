/** 帖子写入表单；公共 AJAX 封装会自动附加当前会话的 Bearer Token。 */

import {ajaxRequest, errorMessages} from "./api.js";

$(function () {
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
                content: $form.find("[name=content]").val(),
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
