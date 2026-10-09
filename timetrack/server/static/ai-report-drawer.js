/** AI report: full-screen loading overlay + slide-in result panel. */
(function () {
  const cfg = window.AI_REPORT_CFG;
  if (!cfg || !cfg.userId || !cfg.day) return;

  const drawer = document.getElementById("ai-report-drawer");
  if (!drawer) return;

  const busyEl = drawer.querySelector(".ai-drawer-busy");
  const backdrop = drawer.querySelector(".ai-drawer-backdrop");
  const panel = drawer.querySelector(".ai-drawer-panel");
  const closeBtn = drawer.querySelector(".ai-drawer-close");
  const errorEl = drawer.querySelector(".ai-drawer-error");
  const contentEl = drawer.querySelector(".ai-drawer-content");
  const metaEl = drawer.querySelector(".ai-drawer-meta");
  const statusChip = document.getElementById("ai-report-chip");
  const generateBtn = document.getElementById("ai-report-generate-btn");
  const viewBtn = document.getElementById("ai-report-view-btn");
  const emailBtn = document.getElementById("ai-report-email-btn");

  let pollTimer = null;
  let drawerOpen = false;

  function statusUrl() {
    return `/admin/user/${cfg.userId}/ai-report/status?day=${encodeURIComponent(cfg.day)}`;
  }

  function postUrl() {
    return `/admin/user/${cfg.userId}/ai-report`;
  }

  function show(el) {
    if (el) el.hidden = false;
  }

  function hide(el) {
    if (el) el.hidden = true;
  }

  function showBusy() {
    drawer.classList.add("is-busy");
    drawer.classList.remove("is-result");
    show(busyEl);
    hide(backdrop);
    hide(panel);
  }

  function showResult() {
    drawer.classList.remove("is-busy");
    drawer.classList.add("is-result");
    hide(busyEl);
    show(backdrop);
    show(panel);
  }

  function openDrawer(mode) {
    drawer.classList.add("is-open");
    drawer.setAttribute("aria-hidden", "false");
    document.body.classList.add("ai-drawer-open");
    drawerOpen = true;
    if (mode === "busy") showBusy();
    else showResult();
  }

  function closeDrawer() {
    drawer.classList.remove("is-open", "is-busy", "is-result");
    drawer.setAttribute("aria-hidden", "true");
    document.body.classList.remove("ai-drawer-open");
    hide(busyEl);
    hide(backdrop);
    hide(panel);
    drawerOpen = false;
    stopPolling();
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function setChip(status) {
    if (!statusChip) return;
    const labels = {
      none: "No report",
      generating: "Generating…",
      ready: "Report ready",
      failed: "Failed",
    };
    statusChip.textContent = labels[status] || status;
    statusChip.className = "ai-report-chip status-" + status;
  }

  function renderState(data, opts) {
    opts = opts || {};
    const status = data.status || "none";
    setChip(status);

    if (viewBtn) viewBtn.hidden = status !== "ready";
    if (emailBtn) emailBtn.hidden = status !== "ready";
    if (generateBtn) {
      generateBtn.textContent = status === "failed" ? "Retry AI report" : "Generate AI report";
      generateBtn.disabled = status === "generating";
    }

    if (status === "generating") {
      if (drawerOpen || opts.forceBusy) {
        openDrawer("busy");
      }
      if (metaEl) metaEl.textContent = "";
      return;
    }

    if (status === "failed") {
      if (drawerOpen || opts.openOnResult) {
        openDrawer("result");
        show(errorEl);
        hide(contentEl);
        errorEl.textContent = data.error_message || "AI report failed. Try again.";
      }
      if (metaEl) metaEl.textContent = "";
      stopPolling();
      return;
    }

    if (status === "ready" && data.summary) {
      if (drawerOpen || opts.openOnResult) {
        openDrawer("result");
        hide(errorEl);
        show(contentEl);
        contentEl.textContent = data.summary;
        let meta = "";
        if (data.created_at) meta += "Generated " + data.created_at.replace("T", " ").slice(0, 16) + " UTC";
        if (data.emailed_at) meta += (meta ? " · " : "") + "Emailed " + data.emailed_at.replace("T", " ").slice(0, 16) + " UTC";
        if (metaEl) metaEl.textContent = meta;
      }
      stopPolling();
      return;
    }

    hide(errorEl);
    hide(contentEl);
    if (metaEl) metaEl.textContent = "";
    stopPolling();
  }

  async function fetchStatus() {
    const resp = await fetch(statusUrl(), {
      headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      credentials: "same-origin",
    });
    if (!resp.ok) throw new Error("Could not load report status");
    return resp.json();
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        const data = await fetchStatus();
        renderState(data, { openOnResult: true });
      } catch (_e) {
        /* keep polling */
      }
    }, 3000);
  }

  async function refreshStatus(openPanel) {
    try {
      const data = await fetchStatus();
      if (openPanel && data.status === "ready") {
        openDrawer("result");
      }
      renderState(data, { openOnResult: openPanel });
      if (data.status === "generating") {
        if (openPanel) openDrawer("busy");
        startPolling();
      }
    } catch (e) {
      if (drawerOpen) {
        openDrawer("result");
        show(errorEl);
        errorEl.textContent = e.message || "Could not load report.";
      }
    }
  }

  async function startGenerate() {
    openDrawer("busy");
    renderState({ status: "generating" }, { forceBusy: true });
    try {
      const resp = await fetch(postUrl(), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Requested-With": "fetch",
        },
        credentials: "same-origin",
        body: JSON.stringify({ action: "generate", day: cfg.day }),
      });
      const data = await resp.json();
      if (!data.ok && data.status !== "generating") {
        renderState(
          { status: "failed", error_message: data.error || data.message || "Could not start." },
          { openOnResult: true }
        );
        return;
      }
      renderState(data, { forceBusy: true });
      startPolling();
    } catch (e) {
      renderState(
        { status: "failed", error_message: e.message || "Request failed." },
        { openOnResult: true }
      );
    }
  }

  async function sendEmail() {
    if (emailBtn) emailBtn.disabled = true;
    try {
      const resp = await fetch(postUrl(), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Requested-With": "fetch",
        },
        credentials: "same-origin",
        body: JSON.stringify({ action: "email", day: cfg.day }),
      });
      const data = await resp.json();
      if (!data.ok) throw new Error(data.error || "Email failed");
      await refreshStatus(false);
    } catch (e) {
      alert(e.message || "Could not email report.");
    } finally {
      if (emailBtn) emailBtn.disabled = false;
    }
  }

  if (generateBtn) generateBtn.addEventListener("click", startGenerate);
  if (viewBtn) viewBtn.addEventListener("click", () => refreshStatus(true));
  if (emailBtn) emailBtn.addEventListener("click", sendEmail);
  if (closeBtn) closeBtn.addEventListener("click", closeDrawer);
  if (backdrop) backdrop.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && drawer.classList.contains("is-open")) closeDrawer();
  });

  fetchStatus().then((data) => {
    renderState(data, {});
    if (data.status === "generating") startPolling();
  }).catch(() => {});
})();
