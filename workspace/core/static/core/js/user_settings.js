// Writes the user's settings through the settings API. Both reject when the
// request fails or the server refuses a value (a key its module does not
// declare, a value its field does not accept), so a caller can roll back. A
// refusal carries the response as `error.response`, whose body names the
// reason; a request that never got an answer has none.
function sendUserSettings(url, method, body, keepalive) {
  return fetch(url, {
    method,
    keepalive,
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
    body: JSON.stringify(body),
  }).then((response) => {
    if (!response.ok) {
      const error = new Error(String(response.status));
      error.response = response;
      throw error;
    }
    return response;
  });
}

// `keepalive` lets the write outlive the page, for a click that also navigates.
window.saveUserSetting = function saveUserSetting(module, key, value, { keepalive = false } = {}) {
  return sendUserSettings(`/api/v1/settings/${module}/${key}`, 'PUT', { value }, keepalive);
};

// Several keys of one module in a single request: all of them are written, or
// none when one is refused.
window.saveUserSettings = function saveUserSettings(module, values) {
  return sendUserSettings(`/api/v1/settings/${module}`, 'PATCH', values, false);
};
