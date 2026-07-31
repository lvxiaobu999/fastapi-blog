/**
 * 文章互动与个人活动页面。
 *
 * 详情页负责记录浏览、切换点赞/收藏；个人活动页根据 URL 中的 tab 查询受保护接口。
 */

import {ajaxRequest, errorMessages, getToken, requireLogin} from "./api.js";
import {setButtonLoading} from "./ui.js";

function renderInteraction(container, state) {
    document.querySelectorAll("[data-view-count]").forEach((node) => { node.textContent = state.view_count; });
    container.querySelector("[data-like-count]").textContent = state.like_count;
    container.querySelector("[data-favorite-count]").textContent = state.favorite_count;
    const likeButton = container.querySelector("[data-like-post]");
    const favoriteButton = container.querySelector("[data-favorite-post]");
    likeButton.classList.toggle("active", state.liked);
    favoriteButton.classList.toggle("active", state.favorited);
    likeButton.setAttribute("aria-pressed", String(state.liked));
    favoriteButton.setAttribute("aria-pressed", String(state.favorited));
}

function bindInteraction() {
    const container = document.querySelector("[data-post-interactions]");
    if (!container) return;
    const postId = container.dataset.postId;

    // auth=true 会在已有 Token 时带上身份；游客没有 Token 时仍可调用公开的浏览接口。
    ajaxRequest({url: `/api/posts/${postId}/view`, method: "POST", auth: true})
        .done((state) => renderInteraction(container, state));

    [["[data-like-post]", "like"], ["[data-favorite-post]", "favorite"]].forEach(([selector, action]) => {
        const button = container.querySelector(selector);
        button.addEventListener("click", () => {
            if (!getToken()) {
                requireLogin("登录后才能点赞或收藏文章。");
                return;
            }
            if (!setButtonLoading(button, true)) return;
            ajaxRequest({url: `/api/posts/${postId}/${action}`, method: "POST", auth: true})
                .done((state) => renderInteraction(container, state))
                .fail((xhr) => window.alert(errorMessages(xhr)))
                .always(() => setButtonLoading(button, false));
        });
    });
}

const tabLabels = {comments: "评论", likes: "赞过", favorites: "收藏", views: "我的足迹"};

function activityItem(item, tab) {
    const article = document.createElement("article");
    article.className = "activity-item";
    const link = document.createElement("a");
    link.href = `/posts/${tab === "comments" ? item.post_id : item.id}`;
    link.textContent = tab === "comments" ? item.post_title : item.title;
    const detail = document.createElement("p");
    detail.textContent = tab === "comments" ? item.content : `${item.view_count} 次浏览`;
    article.append(link, detail);
    return article;
}

function bindActivityPage() {
    const page = document.querySelector("[data-activity-page]");
    if (!page) return;
    if (!getToken()) {
        requireLogin("登录后才能查看个人活动。");
        return;
    }
    const tabs = [...page.querySelectorAll("[data-activity-tab]")];
    const list = page.querySelector("[data-activity-list]");
    const empty = page.querySelector("[data-activity-empty]");

    function load(tab) {
        const selected = tabLabels[tab] ? tab : "comments";
        tabs.forEach((button) => button.classList.toggle("active", button.dataset.activityTab === selected));
        history.replaceState(null, "", `${location.pathname}?tab=${selected}`);
        list.replaceChildren();
        empty.classList.add("d-none");
        const url = selected === "comments" ? "/api/me/activities/comments" : `/api/me/activities/posts?kind=${selected}`;
        ajaxRequest({url, auth: true}).done((items) => {
            items.forEach((item) => list.append(activityItem(item, selected)));
            empty.textContent = `还没有${tabLabels[selected]}记录`;
            empty.classList.toggle("d-none", items.length > 0);
        }).fail((xhr) => {
            empty.textContent = errorMessages(xhr);
            empty.classList.remove("d-none");
        });
    }

    tabs.forEach((button) => button.addEventListener("click", () => load(button.dataset.activityTab)));
    load(new URLSearchParams(location.search).get("tab") ?? "comments");
}

document.addEventListener("DOMContentLoaded", () => {
    bindInteraction();
    bindActivityPage();
});
