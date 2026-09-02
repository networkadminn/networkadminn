/* Productivity bar v7 — tooltips, now-scroll, keyboard fill */
(function () {
  const CAT = {
    productive: { label: "Productive", color: "#5EBA1C" },
    neutral: { label: "Neutral", color: "#42A5F5" },
    unproductive: { label: "Low productive", color: "#FFC107" },
    idle: { label: "Idle", color: "#EF5350" },
    gap: { label: "Fillable gap", color: "#B0BEC5" },
  };

  function fmtMin(v) {
    const n = Number(v) || 0;
    if (n <= 0) return "0m";
    if (n < 1) return Math.round(n * 60) + "s";
    return (Math.round(n * 10) / 10) + "m";
  }

  function boot() {
    const panel = document.getElementById("prodBarPanel");
    const plot = document.getElementById("prodPlot");
    const tip = document.getElementById("prodFloatTip");
    const scroll = document.getElementById("prodBarScroll");
    if (!panel || !plot || !tip || panel.dataset.booted === "1") return;
    panel.dataset.booted = "1";

    const clickable = panel.dataset.clickable === "1";
    const colSel = ".pb7-col";

    function clearHot() {
      plot.querySelectorAll(`${colSel}.is-hot`).forEach((el) => el.classList.remove("is-hot"));
    }

    function placeTip(evt) {
      const pr = panel.getBoundingClientRect();
      let left = evt.clientX - pr.left + 14;
      let top = evt.clientY - pr.top - 12;
      if (left + tip.offsetWidth > pr.width - 8) left = evt.clientX - pr.left - tip.offsetWidth - 14;
      if (top + tip.offsetHeight > pr.height - 8) top = pr.height - tip.offsetHeight - 8;
      tip.style.left = Math.max(8, left) + "px";
      tip.style.top = Math.max(8, top) + "px";
    }

    function showTip(col, evt) {
      const parts = (col.getAttribute("data-tip") || "").split("|");
      const time = parts[0] || "";
      let body = "";

      if (parts[1] === "gap" && parts.length <= 2) {
        body = `<div class="pb7-tip-gap">${clickable ? "Click to fill offline / idle time" : "Idle gap"}</div>`;
      } else if (parts[1] === "requested") {
        body = `<div class="pb7-tip-gap">Time request already submitted</div>`;
      } else {
        const p = parseFloat(parts[1] || 0);
        const n = parseFloat(parts[2] || 0);
        const u = parseFloat(parts[3] || 0);
        const idle = parseFloat(parts[4] || 0);
        const pct = parseFloat(parts[5] || 0);
        const catKey = parts[6] || "idle";
        const app = parts[7] || "";
        const domain = parts[8] || "";
        const isGap = parts[parts.length - 1] === "gap";
        const cat = CAT[catKey] || CAT.idle;
        const appLine = app
          ? `<div class="pb7-tip-app">${app}${domain ? ` · ${domain}` : ""}</div>`
          : "";

        body = `
          ${appLine}
          <div class="pb7-tip-pct">${pct}% productive</div>
          <div class="pb7-tip-cat"><i style="background:${cat.color}"></i>${cat.label}</div>
          <div class="pb7-tip-stack" aria-hidden="true">
            <i class="p" style="flex:${Math.max(p, 0.01)}"></i>
            <i class="n" style="flex:${Math.max(n, 0.01)}"></i>
            <i class="u" style="flex:${Math.max(u, 0.01)}"></i>
            <i class="idle" style="flex:${Math.max(idle, 0.01)}"></i>
          </div>
          <div class="pb7-tip-rows">
            <span>Productive <b>${fmtMin(p)}</b></span>
            <span>Neutral <b>${fmtMin(n)}</b></span>
            <span>Low prod. <b>${fmtMin(u)}</b></span>
            <span>Idle <b>${fmtMin(idle)}</b></span>
          </div>`;
        if (isGap) {
          body += `<div class="pb7-tip-gap">${clickable ? "Fillable — click to add time" : "Fillable gap"}</div>`;
        }
      }

      tip.innerHTML = `<strong>${time}</strong>${body}`;
      tip.hidden = false;
      clearHot();
      col.classList.add("is-hot");
      placeTip(evt);
    }

    plot.addEventListener("mousemove", (evt) => {
      const col = evt.target.closest(colSel);
      if (!col || !plot.contains(col)) {
        tip.hidden = true;
        clearHot();
        return;
      }
      showTip(col, evt);
    });
    plot.addEventListener("mouseleave", () => {
      tip.hidden = true;
      clearHot();
    });

    function goFill(col) {
      if (!clickable || !col) return;
      const href = col.getAttribute("data-href") || col.getAttribute("href");
      if (href) window.location.href = href;
    }

    plot.addEventListener("click", (evt) => {
      const col = evt.target.closest(`${colSel}[data-href], a${colSel}.fillable`);
      goFill(col);
    });

    plot.addEventListener("keydown", (evt) => {
      if (evt.key !== "Enter" && evt.key !== " ") return;
      const col = evt.target.closest(`${colSel}[data-href]`);
      if (!col) return;
      evt.preventDefault();
      goFill(col);
    });

    // Touch: show tip on tap, second tap follows fill links
    let lastTouchCol = null;
    plot.addEventListener(
      "touchstart",
      (evt) => {
        const col = evt.target.closest(colSel);
        if (!col) return;
        const fake = {
          clientX: evt.touches[0].clientX,
          clientY: evt.touches[0].clientY,
        };
        showTip(col, fake);
        if (lastTouchCol === col && col.classList.contains("fillable")) {
          goFill(col);
        }
        lastTouchCol = col;
      },
      { passive: true }
    );

    // Chart fits the panel width — no horizontal scroll.
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
