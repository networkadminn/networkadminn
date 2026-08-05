(() => {
  "use strict";

  const clampPercent = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return 0;
    }
    return Math.max(0, Math.min(100, number));
  };

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-width]").forEach((element) => {
      element.style.width = `${clampPercent(element.dataset.width)}%`;
    });
    document.querySelectorAll("[data-pct]").forEach((element) => {
      element.style.setProperty("--pct", String(clampPercent(element.dataset.pct)));
    });
    document.querySelectorAll("[data-auto-submit]").forEach((input) => {
      input.addEventListener("change", () => input.form?.submit());
    });
  });
})();

