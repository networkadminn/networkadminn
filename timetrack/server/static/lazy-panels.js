(function () {
  function loadPartial(el) {
    var url = el.getAttribute("data-lazy-url");
    if (!url || el.getAttribute("data-lazy-loaded") === "1") return;
    el.setAttribute("data-lazy-loaded", "1");
    el.classList.add("is-lazy-loading");
    fetch(url, {
      headers: { Accept: "text/html", "X-Requested-With": "fetch" },
      credentials: "same-origin",
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("lazy load failed");
        return resp.text();
      })
      .then(function (html) {
        el.innerHTML = html;
        el.classList.remove("is-lazy-loading");
        el.classList.add("is-lazy-ready");
      })
      .catch(function () {
        el.classList.remove("is-lazy-loading");
        el.classList.add("is-lazy-error");
        el.innerHTML =
          '<p class="empty">Could not load this section. <a href="' +
          url.replace(/([?&])partial=[^&]+/, "$1").replace(/[?&]$/, "") +
          '">Refresh</a></p>';
      });
  }

  function loadScriptOnce(src) {
    return new Promise(function (resolve, reject) {
      if (window.Chart) {
        resolve();
        return;
      }
      var existing = document.querySelector('script[data-lazy-chart]');
      if (existing) {
        existing.addEventListener("load", resolve);
        existing.addEventListener("error", reject);
        return;
      }
      var s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.dataset.lazyChart = "1";
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  function initCharts() {
    var host = document.getElementById("charts-panel");
    if (!host || host.getAttribute("data-charts-ready") === "1") return;
    var src = host.getAttribute("data-chartjs-src");
    var rawEl = document.getElementById("charts-data");
    if (!src || !rawEl) return;
    host.setAttribute("data-charts-ready", "1");
    var DATA;
    try {
      DATA = JSON.parse(rawEl.textContent || "{}");
    } catch (e) {
      return;
    }
    loadScriptOnce(src).then(function () {
      var grid = "rgba(15, 41, 28, 0.07)";
      Chart.defaults.color = "#5F7A6A";
      Chart.defaults.borderColor = grid;
      Chart.defaults.font.family = "'Source Sans 3', sans-serif";
      var hourlyEl = document.getElementById("hourly");
      if (hourlyEl) {
        new Chart(hourlyEl, {
          type: "bar",
          data: {
            labels: DATA.labels,
            datasets: [
              { label: "Productive", data: DATA.productive, backgroundColor: "#0B7A4B", borderRadius: 4, maxBarThickness: 22, stack: "day" },
              { label: "Neutral", data: DATA.neutral, backgroundColor: "#94A3B8", borderRadius: 4, maxBarThickness: 22, stack: "day" },
              { label: "Unproductive", data: DATA.unproductive, backgroundColor: "#C0392B", borderRadius: 4, maxBarThickness: 22, stack: "day" },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 400, easing: "easeOutQuart" },
            interaction: { mode: "index", intersect: false },
            scales: {
              x: { stacked: true, grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12, font: { size: 10, weight: "700" } } },
              y: { stacked: true, beginAtZero: true, grid: { color: grid }, ticks: { font: { size: 10, weight: "700" } } },
            },
            plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11, weight: "700" } } } },
          },
        });
      }
      var catsEl = document.getElementById("cats");
      if (catsEl) {
        new Chart(catsEl, {
          type: "doughnut",
          data: {
            labels: ["Productive", "Neutral", "Unproductive", "Idle"],
            datasets: [
              {
                data: [DATA.category.productive, DATA.category.neutral, DATA.category.unproductive, DATA.category.idle],
                backgroundColor: ["#0B7A4B", "#94A3B8", "#C0392B", "#CBD5E1"],
                borderWidth: 0,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 400, easing: "easeOutQuart" },
            plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11, weight: "700" } } } },
          },
        });
      }
    });
  }

  function observe() {
    var nodes = document.querySelectorAll("[data-lazy-url]");
    var chartHost = document.getElementById("charts-panel");
    if (!("IntersectionObserver" in window)) {
      nodes.forEach(loadPartial);
      if (chartHost) initCharts();
      return;
    }
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var el = entry.target;
          io.unobserve(el);
          if (el.id === "charts-panel") initCharts();
          else if (el.hasAttribute("data-lazy-url")) loadPartial(el);
        });
      },
      { rootMargin: "240px 0px" }
    );
    nodes.forEach(function (el) {
      io.observe(el);
    });
    if (chartHost) io.observe(chartHost);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", observe);
  } else {
    observe();
  }
})();
