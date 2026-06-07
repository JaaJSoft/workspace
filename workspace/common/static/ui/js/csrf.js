function getCSRFToken() {
    return document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || '';
}
