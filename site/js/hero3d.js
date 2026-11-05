// Легкий 3D-акцент першого екрана: золотий медальйон M&D, що повільно обертається й нахиляється за курсором.
// Лише прикраса: вмикається після завантаження сторінки на достатньо потужних пристроях; інакше лишається
// звичайний логотип (img.hero-logo). Three.js — власна копія (js/vendor, MIT), завантажується лише тут.
const logo = document.querySelector(".hero-logo");

function canRun() {
  if (!logo) return false;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  const c = navigator.connection;
  if (c && (c.saveData || /(^|-)2g$/.test(c.effectiveType || ""))) return false;
  if (navigator.deviceMemory && navigator.deviceMemory < 4) return false;
  if (navigator.hardwareConcurrency && navigator.hardwareConcurrency < 4) return false;
  try {
    const t = document.createElement("canvas");
    return !!(t.getContext("webgl2") || t.getContext("webgl"));
  } catch (_) {
    return false;
  }
}

// лицьова сторона медальйона — малюємо монограму на canvas тими самими шрифтами, що й сайт
function faceTexture(THREE) {
  const S = 1024, c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d");
  const bg = g.createRadialGradient(S * 0.42, S * 0.36, S * 0.05, S / 2, S / 2, S * 0.5);
  bg.addColorStop(0, "#1b4a3a");
  bg.addColorStop(1, "#0b211a");
  g.fillStyle = bg;
  g.fillRect(0, 0, S, S);
  const gold = g.createLinearGradient(0, 0, S, S);
  gold.addColorStop(0, "#f1d896");
  gold.addColorStop(0.5, "#d4ac5c");
  gold.addColorStop(1, "#a57a33");
  g.strokeStyle = gold;
  g.globalAlpha = 0.55;
  g.lineWidth = 6;
  g.beginPath(); g.arc(S / 2, S / 2, S * 0.4, 0, Math.PI * 2); g.stroke();
  g.globalAlpha = 1;
  g.fillStyle = gold;
  g.textAlign = "center";
  g.textBaseline = "alphabetic";
  g.font = `600 ${S * 0.27}px "Cormorant Garamond", Georgia, serif`;
  g.fillText("M&D", S / 2, S * 0.58);
  g.font = `600 ${S * 0.05}px Manrope, Arial, sans-serif`;
  g.globalAlpha = 0.85;
  g.letterSpacing = `${S * 0.018}px`;
  g.fillText("BEAUTY SALON", S / 2 + S * 0.009, S * 0.71);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  // торець циліндра має UV, повернуті на 90° — вирівнюємо, щоб монограма стояла рівно
  tex.center.set(0.5, 0.5);
  tex.rotation = Math.PI / 2;
  return tex;
}

async function start() {
  const THREE = await import("./vendor/three.module.min.js");
  await document.fonts.ready.catch(() => {});
  const box = logo.getBoundingClientRect();
  const size = Math.round(box.width) || 132;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(size * 1.25, size * 1.25, false); // розмір на екрані задає CSS (.hero-3d), тут — лише роздільність
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 50);
  camera.position.set(0, 0, 5.2);

  // золото без HDR-карти: м'яке «студійне» оточення з градієнта, щоб метал мав відблиски
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envScene = new THREE.Scene();
  envScene.background = new THREE.Color("#1a2a24");
  const lamp = (color, x, y, z, s) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(s, s), new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }));
    m.position.set(x, y, z); m.lookAt(0, 0, 0); envScene.add(m);
  };
  lamp("#fff4dc", -3, 3, 3, 3);
  lamp("#ffe2a8", 3, 1, 2, 2);
  lamp("#6fa58c", 0, -3, 2, 3);
  scene.environment = pmrem.fromScene(envScene, 0.04).texture;

  const goldMat = new THREE.MeshStandardMaterial({ color: "#d9b25f", metalness: 1, roughness: 0.28 });
  const faceMat = new THREE.MeshStandardMaterial({ map: faceTexture(THREE), metalness: 0.15, roughness: 0.55 });

  const medal = new THREE.Group();
  const disk = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 0.12, 96, 1), [goldMat, faceMat, faceMat]);
  disk.rotation.x = Math.PI / 2;
  medal.add(disk);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(1.0, 0.07, 32, 160), goldMat);
  medal.add(rim);
  scene.add(medal);
  // тонке кільце-орбіта навколо медальйона
  const orbit = new THREE.Mesh(new THREE.TorusGeometry(1.24, 0.012, 12, 200), goldMat);
  orbit.rotation.set(1.15, 0.2, 0);
  scene.add(orbit);

  scene.add(new THREE.AmbientLight("#ffffff", 0.35));
  const key = new THREE.DirectionalLight("#fff1d6", 1.6);
  key.position.set(-2, 3, 4);
  scene.add(key);
  const glint = new THREE.PointLight("#ffe7b0", 6, 8);
  scene.add(glint);

  const cv = renderer.domElement;
  cv.className = "hero-3d";
  cv.setAttribute("role", "img");
  cv.setAttribute("aria-label", logo.getAttribute("alt") || "Монограма M&D");
  logo.after(cv);
  logo.classList.add("is-3d");

  // нахил за курсором (на телефоні — лише плавне «дихання»)
  const target = { x: 0, y: 0 };
  window.addEventListener("pointermove", (e) => {
    target.y = (e.clientX / window.innerWidth - 0.5) * 0.9;
    target.x = (e.clientY / window.innerHeight - 0.5) * 0.6;
  }, { passive: true });

  let visible = true, raf = 0;
  const t0 = performance.now();
  const frame = (now) => {
    raf = 0;
    if (!visible || document.hidden) return;
    const t = (now - t0) / 1000;
    medal.rotation.y += (target.y + Math.sin(t * 0.6) * 0.35 - medal.rotation.y) * 0.05;
    medal.rotation.x += (target.x + Math.sin(t * 0.45) * 0.08 - medal.rotation.x) * 0.05;
    orbit.rotation.z = t * 0.25;
    glint.position.set(Math.cos(t * 0.8) * 2.2, Math.sin(t * 0.8) * 1.4, 2.4);
    renderer.render(scene, camera);
    raf = requestAnimationFrame(frame);
  };
  const kick = () => { if (!raf && visible && !document.hidden) raf = requestAnimationFrame(frame); };
  new IntersectionObserver(([en]) => { visible = en.isIntersecting; kick(); }).observe(cv);
  document.addEventListener("visibilitychange", kick);
  renderer.render(scene, camera);
  requestAnimationFrame(() => cv.classList.add("on"));
  kick();
}

if (canRun()) {
  const go = () => start().catch(() => { /* лишається звичайний логотип */ });
  const idle = window.requestIdleCallback || ((f) => setTimeout(f, 300));
  if (document.readyState === "complete") idle(go);
  else window.addEventListener("load", () => idle(go), { once: true });
}
