/* Productivity bar v7 — tooltips, gap-fill modal, keyboard fill */
(function () {
  const CAT = {
    productive: { label: "Productive", color: "#5EBA1C" },
    neutral: { label: "Neutral", color: "#42A5F5" },
    unproductive: { label: "Unproductive", color: "#E53935" },
    rejected: { label: "Rejected", color: "#E57373" },
    gap: { label: "Fillable gap", color: "#B0BEC5" },
  };

  function fmtMin(v) {
    const n = Number(v) || 0;
    if (n <= 0) return "0m";
    if (n < 1) return Math.round(n * 60) + "s";
    return (Math.round(n * 10) / 10) + "m";
  }

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function minToTime(m) {
    const x = Math.max(0, Math.min(1439, Math.round(m)));
    return `${pad2(Math.floor(x / 60))}:${pad2(x % 60)}`;
  }

  function timeToMin(t) {
    if (!t || !t.includes(":")) return 0;
    const [h, m] = t.split(":").map((x) => parseInt(x, 10) || 0);
    return h * 60 + m;
  }

  function decodeAttr(s) {
    if (!s) return "";
    const el = document.createElement("textarea");
    el.innerHTML = s;
    return el.value;
  }

  function initGapFillModal(panel) {
    const modal = document.getElementById("pb7FillModal");
    const form = document.getElementById("pb7FillForm");
    const deleteForm = document.getElementById("pb7FillDeleteForm");
    const deleteBtn = document.getElementById("pb7FillDeleteBtn");
    const requestIdInput = document.getElementById("pb7FillRequestId");
    const hiddenStart = document.getElementById("pb7FillStart");
    const hiddenEnd = document.getElementById("pb7FillEnd");
    const timeStart = document.getElementById("pb7FillTimeStart");
    const timeEnd = document.getElementById("pb7FillTimeEnd");
    const rangeStart = document.getElementById("pb7GapRangeStart");
    const rangeEnd = document.getElementById("pb7GapRangeEnd");
    const sliderFill = document.getElementById("pb7GapSliderFill");
    const noteInput = form?.querySelector('input[name="note"]');
    const descFieldEl = noteInput?.closest(".pb7-offline-field");
    if (
      !modal ||
      !form ||
      !hiddenStart ||
      !hiddenEnd ||
      !timeStart ||
      !timeEnd ||
      !rangeStart ||
      !rangeEnd
    ) {
      return;
    }

    if (modal.parentElement !== document.body) {
      document.body.appendChild(modal);
    }

    let lastFocus = null;
    let savedScrollY = 0;
    let gapDate = panel.dataset.day || "";
    let boundLo = 0;
    let boundHi = 1439;
    let entryStatus = "";
    const deleteUrlTpl = panel.dataset.offlineDeleteUrl || "";
    const trackedEditUrl = panel.dataset.trackedEditUrl || "";
    const trackedDeleteUrl = panel.dataset.trackedDeleteUrl || "";
    const modalTitle = document.getElementById("pb7FillModalTitle");
    const modalLead = document.getElementById("pb7FillModalLead");
    const MODAL_LEAD = {
      gap: "Adjust, specify or delete the whole tracked time period or parts of it.",
      "offline-approved": "Adjust, specify or delete the whole tracked time period or parts of it.",
      "offline-pending": "Adjust, specify or delete the whole tracked time period or parts of it.",
      "offline-rejected": "Adjust, specify or delete the whole tracked time period or parts of it.",
      tracked: "Adjust, specify or delete the whole tracked time period or parts of it.",
    };
    const dialogPanel = modal.querySelector(".pb7-offline-dialog-panel");
    const descField = descFieldEl;
    const prodPick = form.querySelector(".pb7-prod-pick");
    const origStart = document.getElementById("pb7FillOrigStart");
    const origEnd = document.getElementById("pb7FillOrigEnd");
    const deleteDay = document.getElementById("pb7FillDeleteDay");
    const deleteStart = document.getElementById("pb7FillDeleteStart");
    const deleteEnd = document.getElementById("pb7FillDeleteEnd");
    const projectInput = form.querySelector('input[name="project_name"]');
    const taskInput = form.querySelector('input[name="task_name"]');
    let modalMode = "gap";

    function lockPageScroll() {
      savedScrollY = window.scrollY || document.documentElement.scrollTop || 0;
      document.documentElement.classList.add("pb7-fill-modal-open");
      document.body.classList.add("pb7-fill-modal-open");
    }

    function unlockPageScroll() {
      document.documentElement.classList.remove("pb7-fill-modal-open");
      document.body.classList.remove("pb7-fill-modal-open");
      window.scrollTo(0, savedScrollY);
    }

    function closeModal() {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      unlockPageScroll();
      if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    }

    function paintSlider(lo, hi) {
      if (!sliderFill || boundHi <= boundLo) return;
      const span = boundHi - boundLo;
      const left = ((lo - boundLo) / span) * 100;
      const width = ((hi - lo) / span) * 100;
      sliderFill.style.left = `${left}%`;
      sliderFill.style.width = `${Math.max(width, 1)}%`;
    }

    function syncProdPick() {
      form.querySelectorAll(".pb7-prod-opt input[type='radio']").forEach((radio) => {
        const label = radio.closest(".pb7-prod-opt");
        if (label) label.classList.toggle("is-on", radio.checked);
      });
    }

    function syncHidden() {
      let lo = Math.min(parseInt(rangeStart.value, 10), parseInt(rangeEnd.value, 10));
      let hi = Math.max(parseInt(rangeStart.value, 10), parseInt(rangeEnd.value, 10));
      if (hi <= lo) hi = Math.min(boundHi, lo + 1);
      lo = Math.max(boundLo, Math.min(lo, boundHi - 1));
      hi = Math.max(lo + 1, Math.min(hi, boundHi));
      rangeStart.value = String(lo);
      rangeEnd.value = String(hi);
      timeStart.value = minToTime(lo);
      timeEnd.value = minToTime(hi);
      hiddenStart.value = `${gapDate}T${timeStart.value}`;
      hiddenEnd.value = `${gapDate}T${timeEnd.value}`;
      paintSlider(lo, hi);
    }

    function setEditMode(isEdit, requestId, status) {
      entryStatus = status || "";
      if (requestIdInput) requestIdInput.value = isEdit && requestId ? String(requestId) : "";
    }

    function applyModalMode(mode) {
      modalMode = mode;
      const neutralHead =
        mode === "tracked" ||
        mode === "offline-approved" ||
        mode === "offline-pending" ||
        mode === "offline-rejected";
      if (dialogPanel) {
        dialogPanel.classList.toggle("is-tracked-mode", mode === "tracked");
        dialogPanel.classList.toggle("is-modal-neutral-head", neutralHead);
        dialogPanel.classList.toggle("is-offline-approved", mode === "offline-approved");
      }
      if (modalTitle) {
        if (mode === "tracked") modalTitle.textContent = "Edit tracked time";
        else if (mode === "offline-approved") modalTitle.textContent = "Edit time";
        else if (mode === "offline-pending") modalTitle.textContent = "Pending time";
        else if (mode === "offline-rejected") modalTitle.textContent = "Rejected time";
        else modalTitle.textContent = "Offline time";
      }
      if (modalLead) {
        modalLead.textContent = MODAL_LEAD[mode] || MODAL_LEAD.gap;
      }
      if (descField) descField.hidden = mode === "tracked";
      if (prodPick) prodPick.hidden = mode === "tracked";
      const fillKind = form.querySelector('input[name="fill_kind"]');
      if (fillKind) fillKind.value = mode === "tracked" ? "tracked" : "offline";
      if (mode === "tracked" && trackedEditUrl) {
        form.action = trackedEditUrl;
      } else if (panel.dataset.gapBase) {
        form.action = panel.dataset.gapBase;
      }
      if (deleteBtn) {
        deleteBtn.hidden = mode === "gap";
        if (mode === "offline-rejected") deleteBtn.hidden = true;
      }
    }

    function openModalNew(link) {
      gapDate = link.getAttribute("data-gap-date") || panel.dataset.day || "";
      boundLo = parseInt(link.getAttribute("data-gap-start-min") || "0", 10);
      boundHi = parseInt(link.getAttribute("data-gap-end-min") || "1439", 10);
      if (boundHi <= boundLo) boundHi = boundLo + 5;

      const preS = link.getAttribute("data-prefill-start") || "";
      const preE = link.getAttribute("data-prefill-end") || "";
      let lo = boundLo;
      let hi = boundHi;
      if (preS.includes("T")) {
        gapDate = preS.split("T")[0] || gapDate;
        lo = timeToMin(preS.split("T")[1].slice(0, 5));
      }
      if (preE.includes("T")) hi = timeToMin(preE.split("T")[1].slice(0, 5));

      const dayVal = gapDate || panel.dataset.day || "";
      form.reset();
      applyModalMode("gap");
      setEditMode(false);
      if (origStart) origStart.value = "";
      if (origEnd) origEnd.value = "";

      const dayInput = form.querySelector('input[name="day"]');
      if (dayInput) dayInput.value = dayVal;
      const fillKind = form.querySelector('input[name="fill_kind"]');
      if (fillKind) fillKind.value = "offline";
      form.querySelectorAll('input[name="category"]').forEach((radio) => {
        radio.checked = radio.value === "productive";
      });
      syncProdPick();

      rangeStart.min = String(boundLo);
      rangeStart.max = String(boundHi);
      rangeEnd.min = String(boundLo);
      rangeEnd.max = String(boundHi);
      rangeStart.value = String(lo);
      rangeEnd.value = String(hi);

      syncHidden();
      showModal();
    }

    function openModalExisting(btn) {
      const id = btn.getAttribute("data-offline-id") || "";
      const preS = btn.getAttribute("data-prefill-start") || "";
      const preE = btn.getAttribute("data-prefill-end") || "";
      const note = decodeAttr(btn.getAttribute("data-offline-note") || "");
      const cat = btn.getAttribute("data-offline-category") || "productive";
      const status = btn.getAttribute("data-offline-status") || "approved";

      if (preS.includes("T")) gapDate = preS.split("T")[0] || panel.dataset.day || "";
      else gapDate = panel.dataset.day || "";

      let lo = 0;
      let hi = 1439;
      if (preS.includes("T")) lo = timeToMin(preS.split("T")[1].slice(0, 5));
      if (preE.includes("T")) hi = timeToMin(preE.split("T")[1].slice(0, 5));
      if (hi <= lo) hi = lo + 5;

      boundLo = 0;
      boundHi = 1439;

      form.reset();
      if (status === "pending") applyModalMode("offline-pending");
      else if (status === "rejected") applyModalMode("offline-rejected");
      else applyModalMode("offline-approved");
      if (status === "rejected") {
        setEditMode(false, "", status);
        if (requestIdInput) requestIdInput.value = "";
      } else {
        setEditMode(true, id, status);
      }
      if (origStart) origStart.value = "";
      if (origEnd) origEnd.value = "";

      const dayInput = form.querySelector('input[name="day"]');
      if (dayInput) dayInput.value = gapDate;
      if (requestIdInput) requestIdInput.value = id;
      if (noteInput) noteInput.value = note;

      form.querySelectorAll('input[name="category"]').forEach((radio) => {
        radio.checked = radio.value === cat;
      });
      syncProdPick();

      rangeStart.min = String(boundLo);
      rangeStart.max = String(boundHi);
      rangeEnd.min = String(boundLo);
      rangeEnd.max = String(boundHi);
      rangeStart.value = String(lo);
      rangeEnd.value = String(hi);
      syncHidden();

      if (deleteForm && deleteUrlTpl && id) {
        deleteForm.action = deleteUrlTpl.replace(/\/0\/delete$/, `/${id}/delete`);
      }

      showModal();
    }

    function trackedEntryForColumn(col, plotEl) {
      if (!plotEl || !col) return null;
      const colRect = col.getBoundingClientRect();
      const mid = (colRect.left + colRect.right) / 2;
      const entries = plotEl.querySelectorAll(".js-tracked-entry");
      for (const entry of entries) {
        const r = entry.getBoundingClientRect();
        if (mid >= r.left - 1 && mid <= r.right + 1) return entry;
      }
      return null;
    }

    function openModalTracked(btn) {
      if (!btn) return;
      const preS = btn.getAttribute("data-prefill-start") || "";
      const preE = btn.getAttribute("data-prefill-end") || "";
      const trackStart = btn.getAttribute("data-track-start") || "";
      const trackEnd = btn.getAttribute("data-track-end") || "";
      const project = decodeAttr(btn.getAttribute("data-project-name") || "");
      const task = decodeAttr(btn.getAttribute("data-task-name") || "");

      if (preS.includes("T")) gapDate = preS.split("T")[0] || panel.dataset.day || "";
      else gapDate = panel.dataset.day || "";

      let lo = 0;
      let hi = 1439;
      if (preS.includes("T")) lo = timeToMin(preS.split("T")[1].slice(0, 5));
      if (preE.includes("T")) hi = timeToMin(preE.split("T")[1].slice(0, 5));
      if (hi <= lo) hi = lo + 5;

      boundLo = 0;
      boundHi = 1439;

      form.reset();
      applyModalMode("tracked");
      setEditMode(true, "", "");
      if (origStart) origStart.value = trackStart;
      if (origEnd) origEnd.value = trackEnd;

      const dayInput = form.querySelector('input[name="day"]');
      if (dayInput) dayInput.value = gapDate;
      if (projectInput) projectInput.value = project;
      if (taskInput) taskInput.value = task;
      if (noteInput) noteInput.value = "";

      rangeStart.min = String(boundLo);
      rangeStart.max = String(boundHi);
      rangeEnd.min = String(boundLo);
      rangeEnd.max = String(boundHi);
      rangeStart.value = String(lo);
      rangeEnd.value = String(hi);
      syncHidden();
      showModal();
    }

    function showModal() {
      lastFocus = document.activeElement;
      lockPageScroll();
      modal.hidden = false;
      modal.setAttribute("aria-hidden", "false");
      const panelEl = modal.querySelector(".pb7-offline-dialog-panel");
      if (panelEl) panelEl.scrollTop = 0;
      timeStart.focus({ preventScroll: true });
    }

    [rangeStart, rangeEnd].forEach((el) => {
      el.addEventListener("input", syncHidden);
    });
    [timeStart, timeEnd].forEach((el) => {
      el.addEventListener("change", () => {
        rangeStart.value = String(timeToMin(timeStart.value));
        rangeEnd.value = String(timeToMin(timeEnd.value));
        syncHidden();
      });
    });

    form.querySelectorAll('.pb7-prod-opt input[type="radio"]').forEach((radio) => {
      radio.addEventListener("change", syncProdPick);
    });

    document.addEventListener("click", (evt) => {
      const gapLink = evt.target.closest(".js-gap-fill");
      if (gapLink && panel.contains(gapLink)) {
        if (evt.metaKey || evt.ctrlKey || evt.shiftKey || evt.altKey) return;
        evt.preventDefault();
        openModalNew(gapLink);
        return;
      }
      const entryBtn = evt.target.closest(".js-offline-entry");
      if (entryBtn && panel.contains(entryBtn)) {
        evt.preventDefault();
        openModalExisting(entryBtn);
        return;
      }
      const trackedBtn = evt.target.closest(".js-tracked-entry");
      if (trackedBtn && panel.contains(trackedBtn)) {
        evt.preventDefault();
        openModalTracked(trackedBtn);
      }
    });

    function clearTrackedPresenceHover(plotEl) {
      plotEl.querySelectorAll(".js-tracked-entry.is-tracked-hover").forEach((el) => {
        el.classList.remove("is-tracked-hover");
      });
    }

    function syncTrackedPresenceHover(evt, plotEl) {
      clearTrackedPresenceHover(plotEl);
      const trackedBtn = evt.target.closest(".js-tracked-entry");
      if (trackedBtn) {
        trackedBtn.classList.add("is-tracked-hover");
        return;
      }
      const col = evt.target.closest(".pb7-col.js-tracked-col");
      if (col) {
        const entry = trackedEntryForColumn(col, plotEl);
        if (entry) entry.classList.add("is-tracked-hover");
      }
    }

    const plotEl = panel.querySelector("#prodPlot");
    if (plotEl) {
      plotEl.addEventListener("click", (evt) => {
        if (evt.target.closest(".js-gap-fill, .js-offline-entry, .js-tracked-entry")) return;
        const col = evt.target.closest(".pb7-col.js-tracked-col");
        if (!col) return;
        evt.preventDefault();
        evt.stopPropagation();
        const merged = trackedEntryForColumn(col, plotEl);
        openModalTracked(merged || col);
      });
      plotEl.addEventListener("mousemove", (evt) => syncTrackedPresenceHover(evt, plotEl));
      plotEl.addEventListener("mouseleave", () => clearTrackedPresenceHover(plotEl));
    }

    if (deleteBtn && deleteForm) {
      deleteBtn.addEventListener("click", () => {
        if (modalMode === "tracked") {
          if (!trackedDeleteUrl) return;
          const msg = "Remove this tracked time? This cannot be undone.";
          if (!window.confirm(msg)) return;
          deleteForm.action = trackedDeleteUrl;
          const dayVal = form.querySelector('input[name="day"]')?.value || panel.dataset.day || "";
          if (deleteDay) deleteDay.value = dayVal;
          if (deleteStart) deleteStart.value = hiddenStart.value;
          if (deleteEnd) deleteEnd.value = hiddenEnd.value;
          deleteForm.submit();
          return;
        }
        if (!deleteForm.action || deleteForm.action.endsWith("/0/delete")) return;
        const msg =
          entryStatus === "pending"
            ? "Cancel this pending offline time request?"
            : "Remove this offline time entry? This cannot be undone.";
        if (!window.confirm(msg)) return;
        deleteForm.submit();
      });
    }

    modal.querySelectorAll("[data-fill-modal-close]").forEach((el) => {
      el.addEventListener("click", closeModal);
    });

    document.addEventListener("keydown", (evt) => {
      if (!modal.hidden && evt.key === "Escape") {
        evt.preventDefault();
        closeModal();
      }
    });
  }

  function boot() {
    const panel = document.getElementById("prodBarPanel");
    const plot = document.getElementById("prodPlot");
    const tip = document.getElementById("prodFloatTip");
    if (!panel || !plot || !tip || panel.dataset.booted === "1") return;
    panel.dataset.booted = "1";

    const clickable = panel.dataset.clickable === "1";
    const colSel = ".pb7-col";

    if (clickable) initGapFillModal(panel);

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
        body = `<div class="pb7-tip-gap">${clickable ? "Click to request offline time" : "Fillable gap"}</div>`;
      } else if (parts[1] === "requested") {
        const reqCat = CAT[parts[2] || "neutral"] || CAT.neutral;
        body = `<div class="pb7-tip-gap">Pending approval</div>
          <div class="pb7-tip-cat"><i style="background:${reqCat.color}"></i>Will count as ${reqCat.label}</div>`;
      } else if (parts[1] === "rejected") {
        const reqCat = CAT[parts[2] || "neutral"] || CAT.neutral;
        body = `<div class="pb7-tip-gap">Rejected request</div>
          <div class="pb7-tip-cat"><i style="background:${reqCat.color}"></i>Was ${reqCat.label}</div>`;
      } else {
        const p = parseFloat(parts[1] || 0);
        const n = parseFloat(parts[2] || 0);
        const u = parseFloat(parts[3] || 0);
        const pct = parseFloat(parts[5] || 0);
        const catKey = parts[6] || "neutral";
        const app = parts[7] || "";
        const domain = parts[8] || "";
        const isGap = parts[parts.length - 1] === "gap";
        const cat = CAT[catKey] || CAT.neutral;
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
          </div>
          <div class="pb7-tip-rows">
            <span>Productive <b>${fmtMin(p)}</b></span>
            <span>Unproductive <b>${fmtMin(u)}</b></span>
            <span>Neutral <b>${fmtMin(n)}</b></span>
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
      if (!col) {
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
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
