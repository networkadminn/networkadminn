/** Full-page splash while the document loads (Euclidee ring). */
(function () {
  var root = document.getElementById("app-loader");
  if (!root) return;

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var authShell = document.body.classList.contains("auth-shell");
  var fastEntry = sessionStorage.getItem("esstracker-fast-entry") === "1";
  if (fastEntry) {
    try {
      sessionStorage.removeItem("esstracker-fast-entry");
    } catch (e) {}
  }

  document.documentElement.classList.add("app-loader-on");
  document.body.classList.add("app-loader-active");

  var minMs = reduced || authShell || fastEntry ? 0 : 160;
  var started = Date.now();

  function dismissLoader(instant) {
    root.classList.add("is-done");
    document.body.classList.remove("app-loader-active");
    document.documentElement.classList.remove("app-loader-on");
    var crit = document.getElementById("app-loader-critical");
    if (crit) crit.remove();
    root.setAttribute("aria-hidden", "true");
    root.setAttribute("aria-busy", "false");
    var removeMs = instant || reduced ? 0 : 280;
    window.setTimeout(function () {
      if (root.parentNode) root.parentNode.removeChild(root);
    }, removeMs);
  }

  function finish() {
    if (fastEntry || authShell) {
      dismissLoader(true);
      return;
    }
    var wait = Math.max(0, minMs - (Date.now() - started));
    window.setTimeout(function () {
      dismissLoader(false);
    }, wait);
  }

  if (document.readyState === "complete") {
    finish();
  } else {
    window.addEventListener("load", finish, { once: true });
  }
})();
