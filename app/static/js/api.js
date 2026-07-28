/** jQuery.ajax 公共封装；集中处理 JSON、Bearer Header 和 FastAPI 错误信息。 */

const TOKEN_KEY = "blog-access-token";

function responseData(result) {
    // 公共层统一拆掉成功响应信封，让业务脚本继续只关注 data 中的 Token、帖子或用户。
    return result?.success === true ? result.data : result;
}

export function getToken() {
    return localStorage.getItem(TOKEN_KEY);
}

export function saveToken(token) {
    localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
}

export function ajaxRequest({url, method = "GET", data, formEncoded = false, auth = false}) {
    const headers = {};
    if (auth && getToken()) {
        headers.Authorization = `Bearer ${getToken()}`;
    }

    const request = () => $.ajax({
        url,
        method,
        headers,
        data: formEncoded ? data : (data === undefined ? undefined : JSON.stringify(data)),
        contentType: formEncoded ? "application/x-www-form-urlencoded; charset=UTF-8" : "application/json",
        dataType: "json",
    });
    const deferred = $.Deferred();
    const retryAfterRefresh = () => request()
        .done((result) => deferred.resolve(responseData(result)))
        .fail((retryXhr) => deferred.reject(retryXhr));
    request().done((result) => deferred.resolve(responseData(result))).fail((xhr) => {
        if (auth && xhr.status === 401 && url !== "/api/auth/refresh") {
            $.ajax({url: "/api/auth/refresh", method: "POST", dataType: "json"})
                .done((result) => {
                    saveToken(responseData(result).access_token);
                    retryAfterRefresh();
                })
                .fail((refreshXhr) => deferred.reject(refreshXhr));
        } else {
            deferred.reject(xhr);
        }
    });
    return deferred.promise();
}

export function uploadFile(url, fieldName, file) {
    const formData = new FormData();
    formData.append(fieldName, file);
    const headers = {};
    if (getToken()) {
        headers.Authorization = `Bearer ${getToken()}`;
    }
    return $.ajax({
        url,
        method: "POST",
        headers,
        data: formData,
        processData: false,
        contentType: false,
        dataType: "json",
    }).then(responseData);
}

export function errorMessages(xhr) {
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
