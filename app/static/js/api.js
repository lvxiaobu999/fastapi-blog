/** jQuery.ajax 公共封装；集中处理 JSON、Bearer Header 和 FastAPI 错误信息。 */

const TOKEN_KEY = "blog-access-token";

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
        .done((result) => deferred.resolve(result))
        .fail((retryXhr) => deferred.reject(retryXhr));
    request().done((result) => deferred.resolve(result)).fail((xhr) => {
        if (auth && xhr.status === 401 && url !== "/api/auth/refresh") {
            $.ajax({url: "/api/auth/refresh", method: "POST", dataType: "json"})
                .done((result) => {
                    saveToken(result.access_token);
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
    });
}

export function errorMessages(xhr) {
    // 新接口统一从 error 读取；detail 兼容尚未迁移或第三方接口的旧 FastAPI 格式。
    const error = xhr.responseJSON?.error;
    const detail = error?.details ?? xhr.responseJSON?.detail;
    if (Array.isArray(detail)) {
        return detail.map((item) => item.msg).join("；");
    }
    if (typeof error?.message === "string") {
        return error.message;
    }
    return typeof detail === "string" ? detail : "请求失败，请稍后重试。";
}
