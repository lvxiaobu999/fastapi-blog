/**
 * 帖子评论区完整入口。
 *
 * 页面初始评论由 HTTP 获取，页面打开后的新增评论由 WebSocket 推送。WebSocket
 * 建立后先认证，再进入当前 postId 对应的服务端房间；提交者和其他在线用户都通过
 * comment.created 进入同一个渲染函数，避免本地插入与广播插入造成重复。
 */

import {ajaxRequest, getToken, requireLogin} from "./api.js";
import {setButtonLoading} from "./ui.js";

$(function () {
    // comments.js 可能被全站引入；没有评论根节点说明当前不是帖子详情页，直接结束。
    const root = document.querySelector("[data-comments]");
    if (!root) return;

    const postId = Number(root.dataset.postId);
    // ---------- DOM 引用：后续函数只操作这里缓存的评论区节点 ----------
    const list = root.querySelector("[data-comment-list]");
    const empty = root.querySelector("[data-comment-empty]");
    const count = root.querySelector("[data-comment-count]");
    const form = root.querySelector("[data-comment-form]");
    const input = form.querySelector("[name=content]");
    const submit = root.querySelector("[data-comment-submit]");
    const status = root.querySelector("[data-comment-status]");
    const replying = root.querySelector("[data-comment-replying]");
    const replyName = root.querySelector("[data-reply-name]");
    const replyCancel = root.querySelector("[data-reply-cancel]");
    let total = 0;
    // replyTarget 保存“实际点击回复的评论 ID”；null 表示发表顶级评论。
    let replyTarget = null;
    // rootBodies: 顶级评论 ID -> 它下方用于容纳回复的 DOM。
    const rootBodies = new Map();
    // HTTP 历史请求与 WebSocket 并行。如果回复先到、顶级评论后到，先暂存在这里，
    // 等顶级评论渲染完成后再搬入对应讨论串，避免实时消息丢失。
    const pendingReplies = new Map();
    let socket = null;
    // ---------- WebSocket 生命周期状态 ----------
    // reconnectAttempts：意外断线次数，认证成功后清零，最多尝试三次。
    let reconnectAttempts = 0;
    // reconnectTimer：保存延时重连任务，离开页面时必须取消。
    let reconnectTimer = null;
    // closedByPage：区分用户主动离开与网络异常；主动离开不应重新连接。
    let closedByPage = false;
    // connectionReady：收到服务端 authenticated 后才为 true，不能只看 socket 已 open。
    let connectionReady = false;
    // submitting：一条评论从 send 到 submitted/error 之间保持 true，阻止重复发送。
    let submitting = false;

    // ---------- 视图渲染：只接收服务端已经校验过的公开评论对象 ----------

    function formatTime(value) {
        // 服务端返回 ISO 时间；展示格式交给浏览器按中文区域转换。
        return new Intl.DateTimeFormat("zh-CN", {
            year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit",
        }).format(new Date(value));
    }

    function appendComment(comment) {
        // 每条评论都用 createElement/textContent 构建，用户正文不会作为 HTML 执行。
        const article = document.createElement("article");
        article.className = "comment-item";
        const avatar = document.createElement("img");
        avatar.className = "comment-avatar";
        avatar.src = comment.author.image_path;
        avatar.alt = "";
        const body = document.createElement("div");
        body.className = "comment-body";
        const meta = document.createElement("div");
        meta.className = "comment-meta";
        const author = document.createElement("strong");
        author.textContent = comment.author.nickname;
        const time = document.createElement("time");
        time.dateTime = comment.created_at;
        time.textContent = formatTime(comment.created_at);
        const content = document.createElement("p");
        if (comment.reply_to_author) {
            // reply_to_author 表示直接回复对象；它只控制 @昵称，不决定所在讨论串。
            const mention = document.createElement("span");
            mention.className = "comment-mention";
            mention.textContent = `@${comment.reply_to_author.nickname} `;
            content.append(mention, document.createTextNode(comment.content));
        } else {
            content.textContent = comment.content;
        }
        const actions = document.createElement("div");
        actions.className = "comment-actions";
        const replyButton = document.createElement("button");
        replyButton.type = "button";
        replyButton.className = "comment-reply-button";
        replyButton.textContent = "回复";
        replyButton.dataset.replyId = comment.id;
        replyButton.dataset.replyName = comment.author.nickname;
        actions.append(replyButton);
        meta.append(author, time);
        body.append(meta, content, actions);
        article.append(avatar, body);

        if (comment.root_id) {
            // 有 root_id 的回复统一放到顶级评论下面，形成掘金式两层视觉结构。
            const replies = rootBodies.get(comment.root_id);
            if (replies) {
                replies.append(article);
            } else {
                // WebSocket 回复可能早于 HTTP 顶级评论到达，暂存以避免丢失。
                const pending = pendingReplies.get(comment.root_id) ?? [];
                pending.push(article);
                pendingReplies.set(comment.root_id, pending);
            }
        } else {
            // 顶级评论创建 thread，并登记回复容器供后续回复按 root_id 查找。
            const thread = document.createElement("section");
            thread.className = "comment-thread";
            const replies = document.createElement("div");
            replies.className = "comment-replies";
            thread.append(article, replies);
            list.append(thread);
            rootBodies.set(comment.id, replies);
            (pendingReplies.get(comment.id) ?? []).forEach((item) => replies.append(item));
            pendingReplies.delete(comment.id);
        }
        total += 1;
        count.textContent = `${total} 条`;
        empty.classList.add("d-none");
    }

    function clearReplyTarget() {
        // 取消回复后恢复成顶级评论提交状态。
        replyTarget = null;
        replying.classList.add("d-none");
        replyName.textContent = "";
        input.placeholder = "写下你的想法…";
    }

    list.addEventListener("click", (event) => {
        // 事件委托让之后实时插入的回复按钮也能工作，无需逐按钮绑定监听器。
        const button = event.target.closest("[data-reply-id]");
        if (!button || input.disabled) return;
        replyTarget = Number(button.dataset.replyId);
        replyName.textContent = `@${button.dataset.replyName}`;
        replying.classList.remove("d-none");
        input.placeholder = `回复 @${button.dataset.replyName}`;
        input.focus();
    });
    replyCancel.addEventListener("click", clearReplyTarget);

    // ---------- 历史数据：HTTP 负责初始状态，WebSocket 只负责后续增量 ----------

    function loadHistory() {
        // 公开 HTTP 接口不要求 Token；每条响应都复用 appendComment()。
        ajaxRequest({url: `/api/posts/${postId}/comments`})
            .done((comments) => comments.forEach(appendComment))
            .fail(() => { empty.textContent = "评论加载失败，请刷新页面重试。"; });
    }

    // ---------- 连接状态：只有服务端确认 authenticated 后才允许发送 ----------

    function setConnectionState(message, enabled) {
        // 输入框可用条件是“认证完成并且当前没有提交”，而不只是 TCP 连接存在。
        connectionReady = enabled;
        status.textContent = message;
        submit.disabled = !enabled || submitting;
        input.disabled = !enabled || submitting;
    }

    function finishSubmission(saved) {
        // submitted 表示保存成功，会清空正文；error/close 只解锁，保留正文供重试。
        if (!submitting) return;
        submitting = false;
        setButtonLoading(submit, false);
        if (saved) {
            input.value = "";
            clearReplyTarget();
        }
        setConnectionState(status.textContent, connectionReady);
        if (connectionReady) input.focus();
    }

    function handleSocketOpen() {
        // 浏览器 WebSocket API 不能像 AJAX 一样自由设置 Authorization Header，
        // 因此连接建立后用第一条业务消息传递 Access Token。
        socket.send(JSON.stringify({type: "authenticate", token: getToken()}));
    }

    function handleSocketMessage(event) {
        // 服务端以 type 作为应用层协议分发键；WebSocket 本身并不知道评论消息类型。
        const message = JSON.parse(event.data);
        if (message.type === "authenticated") {
            // register 已在服务端完成，收到确认后才允许发表评论。
            reconnectAttempts = 0;
            setConnectionState("实时评论已连接", true);
        } else if (message.type === "comment.created") {
            // 发布者本人也通过广播收到同一条消息，所有客户端共用一个渲染入口，
            // 避免“提交时插入一次、收到广播又插入一次”。
            appendComment(message.data);
        } else if (message.type === "comment.submitted") {
            // 这是只发给提交者的持久化确认，不负责渲染评论。
            finishSubmission(true);
        } else if (message.type === "error") {
            finishSubmission(false);
            status.textContent = message.message;
            if (message.message.includes("登录状态") || message.message.includes("登录后")) {
                requireLogin(message.message);
            }
        }
    }

    function handleSocketClose() {
        // close 事件表示连接“已经断开”。这里的 connect() 是意外断线后的恢复动作，
        // 并不是再次执行关闭；页面主动关闭时 closedByPage=true，所以不会重连。
        finishSubmission(false);
        setConnectionState("评论连接已断开", false);
        if (!closedByPage && getToken() && reconnectAttempts < 3) {
            // 采用 1s、2s、3s 递增等待，避免服务器短暂不可用时立即连续请求。
            reconnectAttempts += 1;
            reconnectTimer = window.setTimeout(connect, 1000 * reconnectAttempts);
        }
    }

    function handleSocketError() {
        // error 事件没有稳定的业务错误信息；关闭后统一走 close 的状态和重连逻辑。
        socket.close();
    }

    function connect() {
        // connect() 每次都创建全新的 WebSocket；重连后也必须重新发送 Token 和认证。
        const token = getToken();
        if (!token) {
            setConnectionState("登录后可以发表评论", false);
            return;
        }
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        // postId 写在 URL 中，后端用它决定 register 到哪个评论房间。
        socket = new WebSocket(`${protocol}//${window.location.host}/api/posts/${postId}/comments/ws`);
        socket.addEventListener("open", handleSocketOpen);
        socket.addEventListener("message", handleSocketMessage);
        socket.addEventListener("close", handleSocketClose);
        socket.addEventListener("error", handleSocketError);
    }

    // ---------- 发送消息：客户端只提交正文和回复目标，其余身份由服务端决定 ----------

    function handleCommentSubmit(event) {
        event.preventDefault();
        const content = input.value.trim();
        // 正文为空、正在提交或连接未打开时都不发送；后端仍会做最终 Schema 校验。
        if (!content || submitting || socket?.readyState !== WebSocket.OPEN) return;
        if (!setButtonLoading(submit, true, "发送中…")) return;
        submitting = true;
        input.disabled = true;
        try {
            // 只提交正文和直接回复目标；作者、帖子和 root_id 均由服务端可信数据决定。
            socket.send(JSON.stringify({type: "comment.create", content, parent_id: replyTarget}));
        } catch (_error) {
            finishSubmission(false);
            status.textContent = "评论发送失败，请重试";
        }
    }

    form.addEventListener("submit", handleCommentSubmit);

    window.addEventListener("beforeunload", () => {
        // 刷新/离开页面属于主动关闭：取消待重连任务，再关闭当前连接。
        closedByPage = true;
        window.clearTimeout(reconnectTimer);
        socket?.close();
    });
    // 历史请求与实时连接并行启动；pendingReplies 会处理两者返回顺序不确定的问题。
    loadHistory();
    connect();
});
