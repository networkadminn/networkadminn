/**
 * timeforge 3D product tour
 * Smooth motion: camera-controls (Sketchfab-style damping + inertia)
 * + interruptible scene transitions + frame-rate independent card focus.
 */
import * as THREE from "three";
import { CSS3DRenderer, CSS3DObject } from "three/addons/renderers/CSS3DRenderer.js";
import CameraControls from "camera-controls";

CameraControls.install({ THREE });

const stage = document.getElementById("tourStage");
const viewport = document.getElementById("tourViewport");
const panelsRoot = document.getElementById("tourPanels");
const dotsRoot = document.getElementById("tourDots");
if (!stage || !viewport || !panelsRoot) {
  console.warn("tour3d: markup missing");
}

const captions = [
  { title: "Team dashboard", text: "Desk time, productivity, effectiveness, and late arrivals — one glance for managers." },
  { title: "My Day", text: "Private arrival, desk time, and live status — no admin clutter." },
  { title: "Productivity bar", text: "5-minute blocks: productive, unproductive, neutral, and fillable idle gaps." },
  { title: "Live presence", text: "Current app, idle state, and last-seen for every teammate." },
  { title: "Gap approvals", text: "Employees explain offline gaps; admins approve before time counts." },
];

const cards = [...panelsRoot.querySelectorAll(".tour3d-card")];
const n = cards.length;
const SCENE_MS = 7500;
const RADIUS = 580;

let index = 0;
let playing = false;
let timer = null;
let progTimer = null;
let raf = 0;
let userOrbiting = false;
let snapLock = false;

const progressEl = document.getElementById("tourProgress");
const playBtn = document.getElementById("tourPlay");
const titleEl = document.getElementById("tourTitle");
const textEl = document.getElementById("tourText");
const stepEl = document.getElementById("tourStep");
const loginEl = document.getElementById("tourLogin");
const captionEl = document.querySelector(".tour3d-caption");

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x010403, 0.0009);
scene.background = new THREE.Color(0x010403);

const camera = new THREE.PerspectiveCamera(38, 1, 1, 6000);

const gl = new THREE.WebGLRenderer({
  antialias: true,
  alpha: false,
  powerPreference: "high-performance",
});
gl.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
gl.outputColorSpace = THREE.SRGBColorSpace;
viewport.appendChild(gl.domElement);

const css = new CSS3DRenderer();
css.domElement.className = "tour3d-css";
viewport.appendChild(css.domElement);

/* —— Lights & room —— */
scene.add(new THREE.AmbientLight(0x1a4a35, 0.55));
const key = new THREE.PointLight(0x12b76a, 2.35, 2600, 2);
key.position.set(0, 480, 220);
scene.add(key);
const fill = new THREE.PointLight(0x4fd1a5, 0.95, 2100, 2);
fill.position.set(-440, 190, -280);
scene.add(fill);
const rim = new THREE.PointLight(0xffffff, 0.38, 1900, 2);
rim.position.set(300, 110, -440);
scene.add(rim);

const floor = new THREE.Mesh(
  new THREE.CircleGeometry(1700, 84),
  new THREE.MeshStandardMaterial({
    color: 0x07150f,
    metalness: 0.42,
    roughness: 0.65,
    emissive: 0x031008,
    emissiveIntensity: 0.48,
  })
);
floor.rotation.x = -Math.PI / 2;
floor.position.y = -175;
scene.add(floor);

const grid = new THREE.GridHelper(2800, 48, 0x0a6b42, 0x0a2a1c);
grid.position.y = -174;
grid.material.transparent = true;
grid.material.opacity = 0.48;
scene.add(grid);

const ringGlow = new THREE.Mesh(
  new THREE.RingGeometry(320, 740, 80),
  new THREE.MeshBasicMaterial({
    color: 0x12b76a,
    transparent: true,
    opacity: 0.1,
    side: THREE.DoubleSide,
  })
);
ringGlow.rotation.x = -Math.PI / 2;
ringGlow.position.y = -173;
scene.add(ringGlow);

const pCount = 240;
const pPos = new Float32Array(pCount * 3);
for (let i = 0; i < pCount; i++) {
  pPos[i * 3] = (Math.random() - 0.5) * 1900;
  pPos[i * 3 + 1] = Math.random() * 820 - 110;
  pPos[i * 3 + 2] = (Math.random() - 0.5) * 1900;
}
const pGeo = new THREE.BufferGeometry();
pGeo.setAttribute("position", new THREE.BufferAttribute(pPos, 3));
const particles = new THREE.Points(
  pGeo,
  new THREE.PointsMaterial({
    color: 0x6ee7b7,
    size: 3.2,
    transparent: true,
    opacity: 0.5,
    sizeAttenuation: true,
  })
);
scene.add(particles);

const orb = new THREE.Mesh(
  new THREE.SphereGeometry(22, 32, 32),
  new THREE.MeshBasicMaterial({ color: 0x12b76a })
);
orb.position.y = -98;
scene.add(orb);
const orbHalo = new THREE.Mesh(
  new THREE.SphereGeometry(60, 32, 32),
  new THREE.MeshBasicMaterial({ color: 0x12b76a, transparent: true, opacity: 0.14 })
);
orbHalo.position.copy(orb.position);
scene.add(orbHalo);

/* —— Product panels (CSS3D) —— */
const objects = [];
const focusScale = new Float32Array(n).fill(0.92);

cards.forEach((card, i) => {
  const obj = new CSS3DObject(card);
  const angle = (i / n) * Math.PI * 2;
  obj.position.set(Math.sin(angle) * RADIUS, 55, Math.cos(angle) * RADIUS);
  obj.userData.index = i;
  obj.userData.angle = angle;
  obj.scale.setScalar(0.92);
  scene.add(obj);
  objects.push(obj);

  const plate = new THREE.Mesh(
    new THREE.PlaneGeometry(480, 340),
    new THREE.MeshBasicMaterial({
      color: 0x0a6b42,
      transparent: true,
      opacity: 0.09,
      side: THREE.DoubleSide,
    })
  );
  plate.position.set(Math.sin(angle) * (RADIUS - 20), 55, Math.cos(angle) * (RADIUS - 20));
  plate.lookAt(0, 55, 0);
  scene.add(plate);
});

/* —— Camera controls: damped orbit (industry standard feel) —— */
const clock = new THREE.Clock();
const controls = new CameraControls(camera, stage);
controls.setTarget(0, 50, 0, false);
controls.smoothTime = 0.45;
controls.draggingSmoothTime = 0.12;
controls.maxSpeed = 12;
controls.azimuthRotateSpeed = 0.42;
controls.polarRotateSpeed = 0.32;
controls.dollySpeed = 0.35;
controls.truckSpeed = 0;
controls.minDistance = 820;
controls.maxDistance = 1480;
controls.minPolarAngle = Math.PI * 0.36;
controls.maxPolarAngle = Math.PI * 0.58;
controls.mouseButtons.left = CameraControls.ACTION.ROTATE;
controls.mouseButtons.middle = CameraControls.ACTION.NONE;
controls.mouseButtons.right = CameraControls.ACTION.NONE;
controls.mouseButtons.wheel = CameraControls.ACTION.DOLLY;
controls.touches.one = CameraControls.ACTION.TOUCH_ROTATE;
controls.touches.two = CameraControls.ACTION.TOUCH_DOLLY_TRUCK;
controls.infinityDolly = false;

dotsRoot.innerHTML = "";
cards.forEach((_, i) => {
  const b = document.createElement("button");
  b.type = "button";
  b.setAttribute("aria-label", `Scene ${i + 1}`);
  b.addEventListener("click", () => {
    go(i, true);
    restart();
  });
  dotsRoot.appendChild(b);
});
const dots = [...dotsRoot.querySelectorAll("button")];

function damp(current, target, smoothTime, dt) {
  // Unity-style exponential damp (frame-rate independent)
  const omega = 2 / Math.max(0.0001, smoothTime);
  const x = omega * dt;
  const exp = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
  return THREE.MathUtils.lerp(current, target, 1 - exp);
}

function wrapPi(a) {
  const t = Math.PI * 2;
  a = ((a % t) + t) % t;
  return a;
}

function nearestFromAzimuth(az) {
  const step = (Math.PI * 2) / n;
  const a = wrapPi(az);
  return Math.round(a / step) % n;
}

function tourDistance() {
  return window.innerWidth < 720 ? 1280 : 1040;
}

function syncCaption(i, animateText = true) {
  const cap = captions[i];
  if (animateText && captionEl) {
    captionEl.classList.remove("is-in");
    captionEl.classList.add("is-out");
    window.setTimeout(() => {
      applyCaption(i, cap);
      captionEl.classList.remove("is-out");
      captionEl.classList.add("is-in");
    }, 160);
  } else {
    applyCaption(i, cap);
    captionEl?.classList.add("is-in");
  }
  cards.forEach((c, ci) => c.classList.toggle("is-front", ci === i));
  dots.forEach((d, di) => d.classList.toggle("is-on", di === i));
}

function applyCaption(i, cap) {
  if (stepEl) {
    stepEl.textContent = `${String(i + 1).padStart(2, "0")} / ${String(n).padStart(2, "0")}`;
  }
  if (titleEl) titleEl.textContent = cap.title;
  if (textEl) textEl.textContent = cap.text;
  if (loginEl) loginEl.hidden = i !== n - 1;
}

function setProgress(pct) {
  if (progressEl) progressEl.style.width = `${pct}%`;
}

function lookAtScene(i, enableTransition) {
  const angle = (i / n) * Math.PI * 2;
  const dist = tourDistance();
  const y = 130;
  return controls.setLookAt(
    Math.sin(angle) * dist,
    y,
    Math.cos(angle) * dist,
    0,
    50,
    0,
    enableTransition
  );
}

function go(i, transition = true) {
  index = ((i % n) + n) % n;
  snapLock = true;
  syncCaption(index, transition);
  setProgress(0);
  const p = lookAtScene(index, transition);
  Promise.resolve(p).finally(() => {
    snapLock = false;
  });
}

function tickProgress() {
  clearInterval(progTimer);
  const start = Date.now();
  progTimer = setInterval(() => {
    setProgress(Math.min(100, ((Date.now() - start) / SCENE_MS) * 100));
  }, 40);
}

function restart() {
  clearInterval(timer);
  clearInterval(progTimer);
  if (!playing || userOrbiting) return;
  tickProgress();
  timer = setInterval(() => {
    go(index + 1, true);
    tickProgress();
  }, SCENE_MS);
}

function resize() {
  const w = stage.clientWidth || window.innerWidth;
  const h = stage.clientHeight || window.innerHeight;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  gl.setSize(w, h, false);
  css.setSize(w, h);
}

function updateCardFocus(dt) {
  const az = controls.azimuthAngle;
  objects.forEach((obj, i) => {
    const want = i === index ? 1.06 : 0.9;
    focusScale[i] = damp(focusScale[i], want, i === index ? 0.28 : 0.4, dt);
    obj.scale.setScalar(focusScale[i]);

    // Billboard toward camera (yaw only)
    const dx = camera.position.x - obj.position.x;
    const dz = camera.position.z - obj.position.z;
    obj.rotation.y = Math.atan2(dx, dz);

    // Subtle float
    const baseY = 55;
    obj.position.y = baseY + Math.sin(performance.now() * 0.0011 + i * 1.1) * (i === index ? 8 : 4);
  });

  // Live highlight while user orbits
  if (userOrbiting && !snapLock) {
    const near = nearestFromAzimuth(az);
    if (near !== index) {
      index = near;
      syncCaption(index, true);
    }
  }
}

let last = performance.now();
function animate(now) {
  if (stage.hidden) {
    raf = 0;
    return;
  }
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;

  const needsRender = controls.update(dt);
  updateCardFocus(dt);

  particles.rotation.y += dt * 0.022;
  orbHalo.scale.setScalar(1 + Math.sin(now * 0.002) * 0.09);
  key.intensity = 2.05 + Math.sin(now * 0.0014) * 0.28;
  ringGlow.rotation.z += dt * 0.04;

  gl.render(scene, camera);
  css.render(scene, camera);
  raf = requestAnimationFrame(animate);

  // keep loop even when controls settle (particles / float)
  void needsRender;
}

function ensureAnim() {
  if (!raf) {
    last = performance.now();
    clock.start();
    raf = requestAnimationFrame(animate);
  }
}

function openTour() {
  stage.hidden = false;
  stage.setAttribute("aria-hidden", "false");
  document.body.classList.add("tour-open");
  playing = true;
  if (playBtn) playBtn.textContent = "❚❚";
  resize();
  controls.enabled = true;
  // Intro: start farther, then ease in
  const angle = 0;
  const far = tourDistance() + 420;
  controls.setLookAt(Math.sin(angle) * far, 180, Math.cos(angle) * far, 0, 50, 0, false);
  index = 0;
  syncCaption(0, false);
  requestAnimationFrame(() => {
    stage.classList.add("is-ready");
    go(0, true);
  });
  restart();
  ensureAnim();
}

function closeTour() {
  playing = false;
  clearInterval(timer);
  clearInterval(progTimer);
  if (raf) cancelAnimationFrame(raf);
  raf = 0;
  stage.classList.remove("is-ready", "is-dragging");
  stage.hidden = true;
  stage.setAttribute("aria-hidden", "true");
  document.body.classList.remove("tour-open");
  userOrbiting = false;
  controls.enabled = false;
}

/* Block orbit when interacting with chrome */
stage.addEventListener("pointerdown", (e) => {
  if (e.target.closest("button, a, .tour3d-top, .tour3d-caption, .tour3d-dots")) {
    controls.enabled = false;
  } else {
    controls.enabled = true;
  }
});
stage.addEventListener("pointerup", () => {
  if (!stage.hidden) controls.enabled = true;
});

controls.addEventListener("controlstart", () => {
  userOrbiting = true;
  stage.classList.add("is-dragging");
  clearInterval(timer);
  clearInterval(progTimer);
});

controls.addEventListener("controlend", () => {
  userOrbiting = false;
  stage.classList.remove("is-dragging");
  const near = nearestFromAzimuth(controls.azimuthAngle);
  go(near, true);
  restart();
});

document.querySelectorAll("[data-tour-open]").forEach((el) => {
  el.addEventListener("click", (e) => {
    e.preventDefault();
    openTour();
  });
});

document.getElementById("tourClose")?.addEventListener("click", closeTour);
document.getElementById("tourPrev")?.addEventListener("click", () => {
  go(index - 1, true);
  restart();
});
document.getElementById("tourNext")?.addEventListener("click", () => {
  go(index + 1, true);
  restart();
});

playBtn?.addEventListener("click", () => {
  playing = !playing;
  playBtn.textContent = playing ? "❚❚" : "▶";
  if (playing) restart();
  else {
    clearInterval(timer);
    clearInterval(progTimer);
  }
});

document.addEventListener("keydown", (e) => {
  if (stage.hidden) return;
  if (e.key === "Escape") closeTour();
  if (e.key === "ArrowRight") {
    go(index + 1, true);
    restart();
  }
  if (e.key === "ArrowLeft") {
    go(index - 1, true);
    restart();
  }
  if (e.key === " ") {
    e.preventDefault();
    playBtn?.click();
  }
});

window.addEventListener("resize", () => {
  if (!stage.hidden) {
    resize();
    lookAtScene(index, false);
  }
});
