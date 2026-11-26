/** Load admin analytics grid after the main dashboard shell paints. */
(function () {
  var root = document.getElementById("admin-viz-root");
  if (!root) return;

  var url = root.getAttribute("data-viz-url");
  if (!url) return;

  function done() {
    root.classList.remove("is-loading");
  }

  fetch(url, {
    headers: { Accept: "text/html", "X-Requested-With": "fetch" },
    credentials: "same-origin",
  })
    .then(function (resp) {
      if (!resp.ok) throw new Error("viz load failed");
      return resp.text();
    })
    .then(function (html) {
      root.innerHTML = html;
      root.setAttribute("aria-busy", "false");
      done();
    })
    .catch(function () {
      root.innerHTML =
        '<p class="viz-load-fail muted">Charts could not load. <a href="' +
        url.replace(/[?&]partial=viz/, "").replace(/\?partial=viz&/, "?").replace(/\?$/, "") +
        '">Refresh the page</a>.</p>';
      done();
    });
})();
