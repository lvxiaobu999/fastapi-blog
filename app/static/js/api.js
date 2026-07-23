/** jQuery.ajax 公共封装；集中处理 JSON、Bearer Header 和 FastAPI 错误信息。 */

const TOKEN_KEY = "blog-access-token";

export function getToken() {
    return sessionStorage.getItem(TOKEN_KEY);
}

export function saveToken(token) {
    sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
    sessionStorage.removeItem(TOKEN_KEY);
}

export function ajaxRequest({url, method = "GET", data, formEncoded = false, auth = false}) {
    const headers = {};
    if (auth && getToken()) {
        headers.Authorization = `Bearer ${getToken()}`;
    }

    return $.ajax({
        url,
        method,
        headers,
        data: formEncoded ? data : (data === undefined ? undefined : JSON.stringify(data)),
        contentType: formEncoded ? "application/x-www-form-urlencoded; charset=UTF-8" : "application/json",
        dataType: "json",
    });
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
    const detail = xhr.responseJSON?.detail;
    if (Array.isArray(detail)) {
        return detail.map((item) => item.msg).join("；");
    }
    return typeof detail === "string" ? detail : "请求失败，请稍后重试。";
}
