(function () {
  var root = document.querySelector("[data-screenshots-root]");
  if (!root) return;

  function panelUrl(href) {
    var u = new URL(href, window.location.origin);
    u.searchParams.set("partial", "screenshots");
    u.hash = "";
    return u.toString();
  }

  function pageUrl(href) {
    var u = new URL(href, window.location.origin);
    u.searchParams.delete("partial");
    return u.pathname + u.search;
  }

  function setLoading(on) {
    root.classList.toggle("is-screenshots-loading", on);
  }

  async function loadPage(href, push) {
    var link = panelUrl(href);
    setLoading(true);
    try {
      var resp = await fetch(link, {
        headers: { Accept: "text/html", "X-Requested-With": "fetch" },
        credentials: "same-origin",
      });
      if (!resp.ok) throw new Error("load failed");
      var html = await resp.text();
      root.innerHTML = html;
      if (push) {
        history.pushState({ screenshots: true }, "", pageUrl(href));
      }
    } catch (err) {
      window.location.assign(href);
    } finally {
      setLoading(false);
    }
  }

  root.addEventListener("click", function (e) {
    var a = e.target.closest(".gallery-pager a[href]");
    if (!a || a.classList.contains("is-on")) return;
    e.preventDefault();
    loadPage(a.href, true);
  });

  window.addEventListener("popstate", function () {
    if (!window.location.pathname.match(/\/admin\/user\/\d+/)) return;
    loadPage(window.location.href, false);
  });
})();
