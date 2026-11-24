// Writes one of the user's settings through the settings API. Rejects when the
// request fails or the server refuses the value (a key its module does not
// declare, a value its field does not accept), so a caller can roll back.
window.saveUserSetting = function saveUserSetting(module, key, value) {
  return fetch(`/api/v1/settings/${module}/${key}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
    body: JSON.stringify({ value }),
  }).then((response) => {
    if (!response.ok) throw new Error(String(response.status));
    return response;
  });
};
