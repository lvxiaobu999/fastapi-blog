/**
 * 前端 API 基础设施。
 *
 * 业务脚本不直接重复处理 Token、统一响应信封和 401，而是调用本模块：
 * 1. ajaxRequest() 发送普通 JSON/表单请求，并在需要时附加 Bearer Token。
 * 2. Access Token 过期时尝试用 HttpOnly Refresh Cookie 换取新 Token并重试一次。
 * 3. 刷新仍失败时清理本地登录状态，通过自定义事件通知 auth.js 打开登录框。
 * 4. errorMessages() 把不同来源的错误响应转换成可直接展示的中文文本。
 */

const TOKEN_KEY = "blog-access-token";
const AUTH_REQUIRED_EVENT = "blog:auth-required";
let refreshPromise = null;

function responseData(result) {
    // 公共层统一拆掉成功响应信封，让业务脚本继续只关注 data 中的 Token、帖子或用户。
    return result?.success === true ? result.data : result;
}

export function getToken() {
    // localStorage 跨页面刷新保留；这里只读取，不代表 Token 一定仍被后端接受。
    return localStorage.getItem(TOKEN_KEY);
}

export function saveToken(token) {
    // 登录或刷新成功后保存短期 Access Token，并立即切换导航栏的首屏样式。
    localStorage.setItem(TOKEN_KEY, token);
    setAuthState(true);
}

export function clearToken() {
    // 清理失效 Token，同时让依赖 data-auth-state 的导航元素切回游客状态。
    localStorage.removeItem(TOKEN_KEY);
    setAuthState(false);
}

export function setAuthState(loggedIn) {
    // html 根节点上的状态只控制前端显示，真正权限仍由后端 JWT 依赖判断。
    document.documentElement.dataset.authState = loggedIn ? "user" : "guest";
    if (!loggedIn) setAdminState(false);
}

export function setAdminState(isAdmin) {
    document.documentElement.dataset.adminState = isAdmin ? "true" : "false";
}

export function requireLogin(message = "登录状态已失效，请重新登录。") {
    // API 基础层不知道登录框具体放在哪里，所以使用事件把 UI 工作交给 auth.js。
    clearToken();
    document.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT, {detail: {message}}));
}

function refreshAccessToken() {
    // 所有并发 401 共享同一次 Refresh。Token 采用单次轮换，若每个业务请求都独立刷新，
    // 只有第一个能成功，其余请求会错误地把刚恢复的登录状态再次清除。
    if (refreshPromise) return refreshPromise;
    const deferred = $.Deferred();
    refreshPromise = deferred.promise();
    $.ajax({url: "/api/auth/refresh", method: "POST", dataType: "json"})
        .done((result) => {
            saveToken(responseData(result).access_token);
            deferred.resolve();
        })
        .fail((xhr) => {
            requireLogin();
            deferred.reject(xhr);
        })
        .always(() => { refreshPromise = null; });
    return deferred.promise();
}

export function ajaxRequest({
    url, method = "GET", data, formEncoded = false, auth = false, refreshAuth = true,
}) {
    // 每次真正发请求时重新读取 Token。Refresh 成功后，重试请求必须使用新 Token，
    // 不能复用第一次请求创建的旧 Authorization Header。
    const request = () => {
        const headers = {};
        if (auth && getToken()) headers.Authorization = `Bearer ${getToken()}`;
        return $.ajax({
            url,
            method,
            headers,
            data: formEncoded ? data : (data === undefined ? undefined : JSON.stringify(data)),
            contentType: formEncoded ? "application/x-www-form-urlencoded; charset=UTF-8" : "application/json",
            dataType: "json",
        });
    };
    // 用 Deferred 把“首次请求 -> 刷新 Token -> 重试”包装成一个 Promise。
    // 调用方只需写一次 done/fail，不必知道中间是否发生过刷新。
    const deferred = $.Deferred();
    const retryAfterRefresh = () => request()
        .done((result) => deferred.resolve(responseData(result)))
        .fail((retryXhr) => {
            if (retryXhr.status === 401) requireLogin();
            deferred.reject(retryXhr);
        });
    // 第一次请求成功直接解包；只有受保护请求的 401 才进入刷新流程。
    request().done((result) => deferred.resolve(responseData(result))).fail((xhr) => {
        if (auth && refreshAuth && xhr.status === 401 && url !== "/api/auth/refresh") {
            refreshAccessToken()
                .done(retryAfterRefresh)
                .fail((refreshXhr) => deferred.reject(refreshXhr));
        } else {
            if (auth && xhr.status === 401) requireLogin();
            deferred.reject(xhr);
        }
    });
    return deferred.promise();
}

export function uploadFile(url, fieldName, file) {
    // 文件上传必须保留浏览器生成的 multipart boundary，因此不能手动设置 contentType。
    const formData = new FormData();
    formData.append(fieldName, file);
    const request = () => {
        const headers = {};
        if (getToken()) headers.Authorization = `Bearer ${getToken()}`;
        return $.ajax({
            url,
            method: "POST",
            headers,
            data: formData,
            processData: false,
            contentType: false,
            dataType: "json",
        });
    };
    const deferred = $.Deferred();
    const retryAfterRefresh = () => request()
        .done((result) => deferred.resolve(responseData(result)))
        .fail((xhr) => {
            if (xhr.status === 401) requireLogin();
            deferred.reject(xhr);
        });
    request().done((result) => deferred.resolve(responseData(result))).fail((xhr) => {
        if (xhr.status !== 401) {
            deferred.reject(xhr);
            return;
        }
        // 图片上传与 JSON API 共享同一个 Refresh Promise，并在重试时读取最新 Token。
        refreshAccessToken()
            .done(retryAfterRefresh)
            .fail((refreshXhr) => deferred.reject(refreshXhr));
    });
    return deferred.promise();
}

export function errorMessages(xhr) {
    // 优先读取项目统一错误契约，再兼容旧接口和 FastAPI 默认 detail。
    const body = xhr.responseJSON;
    if (Array.isArray(body?.errors) && body.errors.length) {
        return body.errors.map((item) => item.message).join("；");
    }
    if (typeof body?.message === "string") {
        return body.message;
    }
    // 兼容尚未迁移的第三方接口和 FastAPI 默认 detail 格式。
    const detail = body?.error?.details ?? body?.detail;
    if (Array.isArray(detail)) {
        return detail.map((item) => item.msg ?? item.message).join("；");
    }
    return typeof detail === "string" ? detail : "请求失败，请稍后重试。";
}
