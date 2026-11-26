(function () {
  var root = document.getElementById("employees-unified");
  if (!root) return;

  var openWrap = null;

  function panelEl(wrap) {
    if (!wrap._kebabPanel) {
      wrap._kebabPanel = wrap.querySelector(".kebab-panel");
    }
    return wrap._kebabPanel;
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
    if (left + w > window.innerWidth - margin) {
      left = window.innerWidth - w - margin;
    }
    if (top + h > window.innerHeight - margin) {
      top = rect.top - h - margin;
    }
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
    var trigger = wrap.querySelector(".kebab-trigger");
    var row = wrap.closest("tr");
    wrap.classList.remove("is-open");
    if (row) row.classList.remove("emp-row-menu-open");
    if (panel) {
      panel.setAttribute("hidden", "");
      panel.style.left = "";
      panel.style.top = "";
      dockPanel(wrap);
    }
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    document.body.classList.remove("emp-kebab-open");
    if (openWrap === wrap) openWrap = null;
  }

  function openMenu(wrap) {
    if (openWrap && openWrap !== wrap) closeMenu(openWrap);
    var panel = panelEl(wrap);
    var trigger = wrap.querySelector(".kebab-trigger");
    if (!panel || !trigger) return;
    var row = wrap.closest("tr");
    wrap.classList.add("is-open");
    if (row) row.classList.add("emp-row-menu-open");
    document.body.appendChild(panel);
    placePanel(trigger, panel);
    trigger.setAttribute("aria-expanded", "true");
    document.body.classList.add("emp-kebab-open");
    openWrap = wrap;
  }

  function onReposition() {
    if (!openWrap) return;
    var trigger = openWrap.querySelector(".kebab-trigger");
    var panel = panelEl(openWrap);
    if (trigger && panel && !panel.hasAttribute("hidden")) {
      placePanel(trigger, panel);
    }
  }

  function isKebabTarget(node) {
    return !!(node && node.closest && (node.closest("[data-emp-kebab]") || node.closest(".kebab-panel")));
  }

  root.addEventListener(
    "click",
    function (evt) {
      var trigger = evt.target.closest(".kebab-trigger");
      if (!trigger || !root.contains(trigger)) return;
      var wrap = trigger.closest("[data-emp-kebab]");
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
    if (isKebabTarget(evt.target)) return;
    closeMenu(openWrap);
  });

  document.addEventListener("keydown", function (evt) {
    if (evt.key === "Escape" && openWrap) closeMenu(openWrap);
  });

  window.addEventListener("resize", onReposition);
  window.addEventListener("scroll", onReposition, true);

  var scrollEl = root.querySelector(".emp-table-scroll");
  if (scrollEl) scrollEl.addEventListener("scroll", onReposition, { passive: true });
})();
