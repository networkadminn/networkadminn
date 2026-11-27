(function () {
  var root = document.getElementById("team-members");
  if (!root) return;

  var openWrap = null;

  function panelEl(wrap) {
    if (!wrap._panel) wrap._panel = wrap.querySelector(".tm-kebab-panel");
    return wrap._panel;
  }

  function placePanel(trigger, panel) {
    var rect = trigger.getBoundingClientRect();
    var margin = 8;
    panel.removeAttribute("hidden");
    panel.style.position = "fixed";
    panel.style.left = "0px";
    panel.style.top = "0px";
    var w = panel.offsetWidth;
    var h = panel.offsetHeight;
    var left = rect.right - w;
    var top = rect.bottom + margin;
    if (left < margin) left = margin;
    if (left + w > window.innerWidth - margin) left = window.innerWidth - w - margin;
    if (top + h > window.innerHeight - margin) top = rect.top - h - margin;
    if (top < margin) top = margin;
    panel.style.left = Math.round(left) + "px";
    panel.style.top = Math.round(top) + "px";
  }

  function dockPanel(wrap) {
    var panel = panelEl(wrap);
    if (panel && panel.parentNode !== wrap) wrap.appendChild(panel);
  }

  function closeMenu(wrap) {
    if (!wrap) return;
    var panel = panelEl(wrap);
    var trigger = wrap.querySelector(".tm-kebab-trigger");
    wrap.classList.remove("is-open");
    if (panel) {
      panel.setAttribute("hidden", "");
      panel.style.left = "";
      panel.style.top = "";
      dockPanel(wrap);
    }
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    if (openWrap === wrap) openWrap = null;
  }

  function openMenu(wrap) {
    if (openWrap && openWrap !== wrap) closeMenu(openWrap);
    var panel = panelEl(wrap);
    var trigger = wrap.querySelector(".tm-kebab-trigger");
    if (!panel || !trigger) return;
    wrap.classList.add("is-open");
    document.body.appendChild(panel);
    placePanel(trigger, panel);
    trigger.setAttribute("aria-expanded", "true");
    openWrap = wrap;
  }

  root.addEventListener(
    "click",
    function (evt) {
      var trigger = evt.target.closest(".tm-kebab-trigger");
      if (!trigger || !root.contains(trigger)) return;
      var wrap = trigger.closest("[data-tm-kebab]");
      if (!wrap) return;
      evt.preventDefault();
      evt.stopPropagation();
      if (wrap.classList.contains("is-open")) closeMenu(wrap);
      else openMenu(wrap);
    },
    true
  );

  document.addEventListener("click", function (evt) {
    if (!openWrap) return;
    if (evt.target.closest("[data-tm-kebab]") || evt.target.closest(".tm-kebab-panel")) return;
    closeMenu(openWrap);
  });

  document.addEventListener("keydown", function (evt) {
    if (evt.key === "Escape" && openWrap) closeMenu(openWrap);
  });

  window.addEventListener("resize", function () {
    if (!openWrap) return;
    var trigger = openWrap.querySelector(".tm-kebab-trigger");
    var panel = panelEl(openWrap);
    if (trigger && panel && !panel.hasAttribute("hidden")) placePanel(trigger, panel);
  });
})();
