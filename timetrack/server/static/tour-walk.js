/**
 * Product walkthrough — spotlight tour of realistic Admin / Employee dashboards.
 * Smooth pointer + morphing highlight (box-shadow hole technique).
 */
(function () {
  "use strict";

  var stage = document.getElementById("productTour");
  if (!stage) return;

  var spot = document.getElementById("ptourSpot");
  var tip = document.getElementById("ptourTip");
  var tipTitle = document.getElementById("ptourTipTitle");
  var tipText = document.getElementById("ptourTipText");
  var tipStep = document.getElementById("ptourTipStep");
  var tipNext = document.getElementById("ptourTipNext");
  var tipPrev = document.getElementById("ptourTipPrev");
  var tipSkip = document.getElementById("ptourTipSkip");
  var pointer = document.getElementById("ptourPointer");
  var viewAdmin = document.getElementById("ptourAdmin");
  var viewEmp = document.getElementById("ptourEmp");
  var roleLabel = document.getElementById("ptourRole");
  var progress = document.getElementById("ptourProgress");

  var ADMIN_STEPS = [
    {
      sel: "[data-pt='a-welcome']",
      title: "Admin dashboard",
      text: "After you sign in as admin, this is your home — the full team overview for today.",
      place: "bottom",
    },
    {
      sel: "[data-pt='a-filters']",
      title: "Pick day & team",
      text: "Jump between days, filter by team, export CSV, or open alerts — same controls as the live app.",
      place: "bottom",
    },
    {
      sel: "[data-pt='a-rings']",
      title: "Productivity & effectiveness",
      text: "Big rings show average productivity and how close the team is to the daily hour goal.",
      place: "right",
    },
    {
      sel: "[data-pt='a-attendance']",
      title: "Who’s in today",
      text: "Online, present, late, and absent counts — click through to the employee table.",
      place: "left",
    },
    {
      sel: "[data-pt='a-split']",
      title: "Team time split",
      text: "See productive, neutral, unproductive, and idle time across the whole team at a glance.",
      place: "top",
    },
    {
      sel: "[data-pt='a-live']",
      title: "Live now",
      text: "Who’s active right now and which app they’re in — refreshes while agents sync.",
      place: "top",
    },
    {
      sel: "[data-pt='a-leaders']",
      title: "Leaderboards",
      text: "Late arrivals, most productive, most effective, and attention-needed rankings.",
      place: "top",
    },
    {
      sel: "[data-pt='a-table']",
      title: "Employees table",
      text: "Click any person to open their full day — timeline, apps, websites, and screenshots.",
      place: "top",
      click: true,
    },
  ];

  var EMP_STEPS = [
    {
      sel: "[data-pt='e-welcome']",
      title: "My Day",
      text: "Employees land here after login — a private view of only their own day.",
      place: "bottom",
    },
    {
      sel: "[data-pt='e-kpis']",
      title: "Your day metrics",
      text: "Arrival, live status, desk time, and productive hours — updated as the desktop agent syncs.",
      place: "bottom",
    },
    {
      sel: "[data-pt='e-bar']",
      title: "Productivity timeline",
      text: "Color blocks show productive, neutral, and unproductive time across office hours.",
      place: "top",
    },
    {
      sel: "[data-pt='e-gap']",
      title: "Fill an idle gap",
      text: "Empty striped gaps are clickable. Explain offline or idle time — admin approves before it counts.",
      place: "top",
      click: true,
    },
    {
      sel: "[data-pt='e-requests']",
      title: "Your requests",
      text: "Track pending, approved, and rejected gap fills. Privacy tip: apps & screenshots stay admin-only.",
      place: "top",
    },
    {
      sel: "[data-pt='e-fill']",
      title: "Fill gap anytime",
      text: "Use Fill gap in the toolbar or sidebar to add offline time manually.",
      place: "left",
      click: true,
    },
  ];

  var role = "admin";
  var steps = ADMIN_STEPS;
  var index = 0;
  var open = false;
  var animPointer = null;

  function $(sel, root) {
    return (root || stage).querySelector(sel);
  }

  function setRole(r) {
    role = r;
    steps = r === "employee" ? EMP_STEPS : ADMIN_STEPS;
    if (viewAdmin) viewAdmin.hidden = r !== "admin";
    if (viewEmp) viewEmp.hidden = r !== "employee";
    if (roleLabel) roleLabel.textContent = r === "employee" ? "Employee · My Day" : "Admin · Dashboard";
    stage.classList.toggle("is-employee", r === "employee");
    stage.classList.toggle("is-admin", r === "admin");
  }

  function tipPlacement(el, place) {
    var r = el.getBoundingClientRect();
    var tw = tip.offsetWidth || 320;
    var th = tip.offsetHeight || 160;
    var pad = 16;
    var x = r.left + r.width / 2 - tw / 2;
    var y = r.bottom + 14;
    tip.dataset.place = place || "bottom";

    if (place === "top") {
      y = r.top - th - 14;
    } else if (place === "left") {
      x = r.left - tw - 14;
      y = r.top + r.height / 2 - th / 2;
    } else if (place === "right") {
      x = r.right + 14;
      y = r.top + r.height / 2 - th / 2;
    }

    x = Math.max(pad, Math.min(x, window.innerWidth - tw - pad));
    y = Math.max(pad, Math.min(y, window.innerHeight - th - pad));
    tip.style.left = x + "px";
    tip.style.top = y + "px";
    tip.style.opacity = "1";
  }

  function moveSpotlight(el) {
    var r = el.getBoundingClientRect();
    var pad = 10;
    var x = Math.max(8, r.left - pad);
    var y = Math.max(8, r.top - pad);
    var w = Math.min(window.innerWidth - x - 8, r.width + pad * 2);
    var h = Math.min(window.innerHeight - y - 8, r.height + pad * 2);
    spot.style.left = x + "px";
    spot.style.top = y + "px";
    spot.style.width = w + "px";
    spot.style.height = h + "px";
    spot.classList.add("is-on");
  }

  function movePointer(el, click) {
    var r = el.getBoundingClientRect();
    var tx = r.left + r.width * 0.55;
    var ty = r.top + r.height * 0.45;
    pointer.classList.add("is-on");
    pointer.style.left = tx + "px";
    pointer.style.top = ty + "px";
    pointer.classList.toggle("is-click", !!click);
    if (animPointer) clearTimeout(animPointer);
    if (click) {
      animPointer = setTimeout(function () {
        pointer.classList.add("is-press");
        setTimeout(function () {
          pointer.classList.remove("is-press");
        }, 220);
      }, 480);
    }
  }

  function syncProgress() {
    var pct = ((index + 1) / steps.length) * 100;
    if (progress) progress.style.width = pct + "%";
    if (tipStep) tipStep.textContent = index + 1 + " / " + steps.length;
    if (tipNext) tipNext.textContent = index >= steps.length - 1 ? "Finish" : "Next";
    if (tipPrev) tipPrev.disabled = index === 0;
  }

  function go(i) {
    if (i < 0 || i >= steps.length) return;
    index = i;
    var step = steps[index];
    var el = $(step.sel);
    if (!el) {
      if (index < steps.length - 1) return go(index + 1);
      return closeTour();
    }

    el.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    syncProgress();

    // Wait a beat for scroll, then spotlight
    requestAnimationFrame(function () {
      setTimeout(function () {
        moveSpotlight(el);
        tipTitle.textContent = step.title;
        tipText.textContent = step.text;
        tip.classList.add("is-on");
        tipPlacement(el, step.place);
        movePointer(el, step.click);
      }, 280);
    });
  }

  function openTour(r) {
    setRole(r || "admin");
    index = 0;
    open = true;
    stage.hidden = false;
    stage.setAttribute("aria-hidden", "false");
    document.body.classList.add("ptour-open");
    tip.style.opacity = "0";
    tip.style.left = "-9999px";
    tip.style.top = "0";
    spot.classList.remove("is-on");
    pointer.classList.remove("is-on", "is-click", "is-press");
    requestAnimationFrame(function () {
      stage.classList.add("is-ready");
      go(0);
    });
  }

  function closeTour() {
    open = false;
    stage.classList.remove("is-ready");
    tip.classList.remove("is-on");
    spot.classList.remove("is-on");
    pointer.classList.remove("is-on");
    document.body.classList.remove("ptour-open");
    stage.hidden = true;
    stage.setAttribute("aria-hidden", "true");
  }

  function next() {
    if (index >= steps.length - 1) closeTour();
    else go(index + 1);
  }

  function prev() {
    if (index > 0) go(index - 1);
  }

  document.querySelectorAll("[data-tour-open]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      openTour(btn.getAttribute("data-tour-role") || "admin");
    });
  });

  document.getElementById("ptourClose")?.addEventListener("click", closeTour);
  tipSkip?.addEventListener("click", closeTour);
  tipNext?.addEventListener("click", next);
  tipPrev?.addEventListener("click", prev);

  document.getElementById("ptourAsAdmin")?.addEventListener("click", function () {
    openTour("admin");
  });
  document.getElementById("ptourAsEmp")?.addEventListener("click", function () {
    openTour("employee");
  });

  document.addEventListener("keydown", function (e) {
    if (!open) return;
    if (e.key === "Escape") closeTour();
    if (e.key === "ArrowRight" || e.key === "Enter") next();
    if (e.key === "ArrowLeft") prev();
  });

  window.addEventListener(
    "resize",
    function () {
      if (!open) return;
      var step = steps[index];
      var el = step && $(step.sel);
      if (el) {
        moveSpotlight(el);
        tipPlacement(el, step.place);
        movePointer(el, step.click);
      }
    },
    { passive: true }
  );
})();
