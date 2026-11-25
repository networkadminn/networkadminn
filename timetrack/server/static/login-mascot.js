/**
 * Readme-style owl login: eyes look at username caret; paws cover eyes on password.
 */
(function () {
  var mascot = document.getElementById("loginMascot") || document.getElementById("forgotMascot");
  var form = document.getElementById("loginForm") || document.getElementById("forgotForm");
  var userInput = document.getElementById("loginUsername") || document.getElementById("forgotIdentity");
  var passInput = document.getElementById("loginPassword");
  var passToggle = document.getElementById("loginPasswordToggle");
  if (!mascot || !userInput) return;

  var pupilGroup = mascot.querySelector(".login-mascot-pupils");
  var headWrap = mascot.querySelector(".login-mascot-head-wrap");
  var pawAnchor = mascot.querySelector(".login-owl-paws");
  var owlBox = document.getElementById("loginOwlBox") || mascot.querySelector(".login-owl-box");
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var passwordVisible = false;
  var measureCanvas = document.createElement("canvas");
  var measureCtx = measureCanvas.getContext("2d");
  var pupilMax = 14;
  var usernameTrackChars = 9;
  var pupilLookDown = 5.5;
  var pupilX = 0;
  var pupilY = 0;
  var targetX = 0;
  var targetY = 0;
  var pupilAnimId = 0;

  function applyPupilOffset(x, y) {
    if (!pupilGroup) return;
    pupilGroup.setAttribute("transform", "translate(" + x + " " + y + ")");
  }

  function setPupilTarget(x, y) {
    targetX = x;
    targetY = y;
    if (reduceMotion) {
      pupilX = x;
      pupilY = y;
      applyPupilOffset(x, y);
      return;
    }
    if (!pupilAnimId) pupilAnimId = window.requestAnimationFrame(stepPupils);
  }

  function stepPupils() {
    pupilAnimId = 0;
    var dx = targetX - pupilX;
    var dy = targetY - pupilY;
    if (Math.abs(dx) < 0.06 && Math.abs(dy) < 0.06) {
      pupilX = targetX;
      pupilY = targetY;
      applyPupilOffset(pupilX, pupilY);
      return;
    }
    pupilX += dx * 0.055;
    pupilY += dy * 0.055;
    applyPupilOffset(pupilX, pupilY);
    pupilAnimId = window.requestAnimationFrame(stepPupils);
  }

  function isPrivateState() {
    return mascot.classList.contains("is-shy") || mascot.classList.contains("is-peek");
  }

  function syncPawLayout() {
    if (!pawAnchor || !owlBox) return;
    var railRect = mascot.getBoundingClientRect();
    var boxRect = owlBox.getBoundingClientRect();
    var pawH = pawAnchor.offsetHeight || 40;
    var rim = boxRect.top - railRect.top - pawH + 13;
    rim = Math.max(24, Math.min(88, Math.round(rim)));
    mascot.style.setProperty("--login-owl-rim-offset", rim + "px");
    if (headWrap) {
      var eye = mascot.querySelector(".login-mascot-eye");
      if (eye) {
        var pawRect = pawAnchor.getBoundingClientRect();
        var eyeRect = eye.getBoundingClientRect();
        var lift = Math.round(pawRect.top - eyeRect.top + 10);
        lift = Math.max(58, Math.min(94, lift));
        mascot.style.setProperty("--paw-cover-lift", lift + "px");
      }
    }
  }

  function caretPoint() {
    var val = userInput.value || "";
    var caret = userInput.selectionStart != null ? userInput.selectionStart : val.length;
    var style = window.getComputedStyle(userInput);
    measureCtx.font = style.font;
    var textW = measureCtx.measureText(val.slice(0, caret)).width;
    var rect = userInput.getBoundingClientRect();
    var padL = parseFloat(style.paddingLeft) || 0;
    var field = userInput.closest(".login-owl-field");
    var icon = field ? field.querySelector(".login-field-icon") : null;
    var iconPad = icon ? icon.getBoundingClientRect().width + 12 : 0;
    return {
      x: rect.left + padL + iconPad + textW,
      y: rect.top + rect.height * 0.55,
    };
  }

  function lookAt(clientX, clientY) {
    if (!headWrap || isPrivateState()) return;
    var r = headWrap.getBoundingClientRect();
    if (!r.width) return;
    var eyeX = r.left + r.width * 0.5;
    var eyeY = r.top + r.height * 0.56;
    var dx = clientX - eyeX;
    var dy = clientY - eyeY;
    var angle = Math.atan2(dy, dx);
    var pull = Math.min(pupilMax, Math.hypot(dx, dy) * 0.085);
    setPupilTarget(Math.cos(angle) * pull, Math.sin(angle) * pull);
  }

  function usernameCaretProgress() {
    var val = userInput.value || "";
    var caret = userInput.selectionStart != null ? userInput.selectionStart : val.length;
    var style = window.getComputedStyle(userInput);
    measureCtx.font = style.font;
    var caretW = measureCtx.measureText(val.slice(0, caret)).width;
    var refW = measureCtx.measureText(Array(usernameTrackChars + 1).join("n")).width;
    if (!refW) refW = 1;
    return Math.max(0, Math.min(1, caretW / refW));
  }

  function trackFromUsername() {
    if (isPrivateState()) return;
    var val = (userInput.value || "").trim();
    if (!val.length) {
      setPupilTarget(-pupilMax * 0.92, pupilLookDown);
      return;
    }
    var t = usernameCaretProgress();
    var x = (t * 2 - 0.28) * pupilMax;
    x = Math.max(-pupilMax, Math.min(pupilMax, x));
    var y = pupilLookDown + t * 0.8;
    setPupilTarget(x, y);
  }

  function updateFormReady() {
    if (!form) return;
    var ok;
    if (passInput) {
      ok =
        (userInput.value || "").trim().length >= 1 && (passInput.value || "").length >= 1;
    } else {
      ok = (userInput.value || "").trim().length >= 1;
    }
    form.classList.toggle("is-ready", ok);
  }

  function refreshMascot() {
    mascot.classList.remove("is-watching", "is-shy", "is-peek");

    if (passInput && passwordVisible && document.activeElement === passInput) {
      mascot.classList.add("is-peek");
      setPupilTarget(5, pupilLookDown + 0.5);
      syncPawLayout();
      return;
    }
    if (passInput && document.activeElement === passInput) {
      mascot.classList.add("is-shy");
      pupilX = 0;
      pupilY = 0;
      targetX = 0;
      targetY = 0;
      applyPupilOffset(0, 0);
      syncPawLayout();
      return;
    }
    if (document.activeElement === userInput) {
      mascot.classList.add("is-watching");
      trackFromUsername();
      syncPawLayout();
      return;
    }
    setPupilTarget(0, 0);
    syncPawLayout();
  }

  function setPasswordVisible(show) {
    passwordVisible = show;
    passInput.type = show ? "text" : "password";
    if (passToggle) {
      passToggle.setAttribute("aria-pressed", show ? "true" : "false");
      passToggle.textContent = show ? "Hide" : "Show";
      passToggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
    }
    refreshMascot();
  }

  userInput.addEventListener("focus", function () {
    refreshMascot();
    updateFormReady();
  });
  userInput.addEventListener("input", function () {
    trackFromUsername();
    updateFormReady();
  });
  userInput.addEventListener("keyup", trackFromUsername);
  userInput.addEventListener("click", trackFromUsername);
  userInput.addEventListener("select", trackFromUsername);

  function afterLoginFieldBlur() {
    window.setTimeout(function () {
      var active = document.activeElement;
      if (active === passInput || active === userInput) return;
      refreshMascot();
    }, 0);
  }

  userInput.addEventListener("blur", afterLoginFieldBlur);
  if (passInput) {
    passInput.addEventListener("focus", refreshMascot);
    passInput.addEventListener("blur", afterLoginFieldBlur);
    passInput.addEventListener("input", updateFormReady);
  }

  if (passToggle && passInput) {
    passToggle.addEventListener("click", function (e) {
      e.preventDefault();
      setPasswordVisible(!passwordVisible);
      passInput.focus();
    });
  }

  window.addEventListener("resize", syncPawLayout);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () {
      syncPawLayout();
      refreshMascot();
    });
  }

  syncPawLayout();
  refreshMascot();
  updateFormReady();

  if (form) {
    var submitBtn = form.querySelector(".login-submit-btn");
    form.addEventListener("submit", function () {
      if (!form.checkValidity()) return;
      try {
        sessionStorage.setItem("esstracker-fast-entry", "1");
      } catch (e) {}
      if (submitBtn) {
        submitBtn.setAttribute("aria-busy", "true");
      }
      window.setTimeout(function () {
        if (submitBtn) submitBtn.disabled = true;
      }, 0);
    });
  }
})();
