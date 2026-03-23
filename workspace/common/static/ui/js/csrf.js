function getCSRFToken() {
    return document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
}
