// Session expiry handler: redirect to login on expired session
// (alpine-ajax 401 + global fetch wrapper).
//
// Reads the login URL from `<body data-login-url="...">` so this file can
// be served as a plain static asset without Django template rendering.
(function () {
  const loginUrl = document.body?.dataset?.loginUrl || '/login';
  let redirecting = false;

  function redirectToLogin() {
    if (redirecting) return;
    redirecting = true;
    const next = window.location.pathname + window.location.search + window.location.hash;
    window.location.href = loginUrl + '?next=' + encodeURIComponent(next);
  }

  // Alpine AJAX: intercept 401 responses via ajax:error
  document.addEventListener('ajax:error', function (e) {
    if (e.detail === 'login_required') redirectToLogin();
  });

  // Wrap global fetch to detect session expiry on ALL async calls.
  // Two cases:
  //   1. Middleware returned 401 (request had AJAX headers)
  //   2. fetch() silently followed a 302 to /login (no AJAX headers)
  const _origFetch = window.fetch;
  window.fetch = function () {
    return _origFetch.apply(this, arguments).then(function (resp) {
      if (resp.status === 401) redirectToLogin();
      if (resp.redirected && new URL(resp.url).pathname === loginUrl) redirectToLogin();
      return resp;
    });
  };
})();
