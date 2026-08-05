(() => {
  "use strict";

  const clampPercent = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return 0;
    }
    return Math.max(0, Math.min(100, number));
  };

  const applyDynamicStyles = () => {
    document.querySelectorAll("[data-width]").forEach((element) => {
      element.style.width = `${clampPercent(element.dataset.width)}%`;
    });
    document.querySelectorAll("[data-pct]").forEach((element) => {
      element.style.setProperty("--pct", String(clampPercent(element.dataset.pct)));
    });
  };

  const enableDateAutoSubmit = () => {
    document.querySelectorAll("[data-auto-submit]").forEach((input) => {
      input.addEventListener("change", () => input.form?.submit());
    });
  };

  const enableLinkedRows = () => {
    document.querySelectorAll("[data-href]").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest("a, button, input, label, select, textarea")) {
          return;
        }
        window.location.assign(row.dataset.href);
      });
    });
  };

  const renderCharts = () => {
    const holder = document.getElementById("chart-data");
    if (!holder || typeof Chart === "undefined") {
      return;
    }

    let data;
    try {
      data = JSON.parse(holder.dataset.charts || "{}");
    } catch {
      return;
    }

    const gridColor = "rgba(255,255,255,0.06)";
    Chart.defaults.color = "#93a0b5";
    Chart.defaults.borderColor = gridColor;

    new Chart(document.getElementById("hourly"), {
      type: "bar",
      data: {
        labels: data.labels,
        datasets: [
          { label: "Productive", data: data.productive, backgroundColor: "#2ecc71" },
          { label: "Neutral", data: data.neutral, backgroundColor: "#7f8c9b" },
          { label: "Unproductive", data: data.unproductive, backgroundColor: "#e74c3c" },
          { label: "Idle", data: data.idle, backgroundColor: "#3a4457" },
        ],
      },
      options: {
        responsive: true,
        scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
        plugins: { legend: { position: "bottom" } },
      },
    });

    new Chart(document.getElementById("cats"), {
      type: "doughnut",
      data: {
        labels: ["Productive", "Unproductive", "Neutral", "Idle"],
        datasets: [
          {
            data: [
              data.category.productive,
              data.category.unproductive,
              data.category.neutral,
              data.category.idle,
            ],
            backgroundColor: ["#2ecc71", "#e74c3c", "#7f8c9b", "#3a4457"],
          },
        ],
      },
      options: { plugins: { legend: { position: "bottom" } } },
    });

    new Chart(document.getElementById("apps"), {
      type: "bar",
      data: {
        labels: data.apps.labels,
        datasets: [{ label: "Minutes", data: data.apps.minutes, backgroundColor: "#4f8cff" }],
      },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true } },
      },
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    applyDynamicStyles();
    enableDateAutoSubmit();
    enableLinkedRows();
    renderCharts();
  });
})();

