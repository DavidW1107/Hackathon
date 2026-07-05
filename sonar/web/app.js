import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

const GREEN = 0x39ff88, GREY = 0x8a97a0, HARD = 0xb8c2c9;
const params = new URLSearchParams(location.search);
const sensorUrl = params.get("sensor");          // e.g. ?sensor=http://localhost:8765

// ---------- scene ----------
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x04060a);
scene.fog = new THREE.FogExp2(0x04060a, 0.055);

const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 100);
camera.position.set(0, 3.2, -4.2);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
document.body.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0.7, 2.6);
controls.enableDamping = true;
controls.update();

// bloom = the matrix glow
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
composer.addPass(new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.8, 0.5, 0.35));

// floor grid receding into the void
const grid = new THREE.GridHelper(16, 32, 0x1c3a2b, 0x0f1f18);
grid.position.z = 4;
scene.add(grid);

// sensor node at origin + forward cone
const node = new THREE.Mesh(
  new THREE.SphereGeometry(0.12, 16, 16),
  new THREE.MeshBasicMaterial({ color: GREEN }));
scene.add(node);
const FOV = 50;
// forward field-of-view as two ground lines (a filled cone floods the void green)
const wedge = new THREE.Group();
for (const s of [-1, 1]) {
  const a = s * FOV * Math.PI / 180;
  const pts = [new THREE.Vector3(0, 0.02, 0), new THREE.Vector3(Math.sin(a) * 6, 0.02, Math.cos(a) * 6)];
  wedge.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: 0x1f8a52, transparent: true, opacity: 0.5 })));
}
scene.add(wedge);

const targetGroup = new THREE.Group();
const clutterGroup = new THREE.Group();
scene.add(targetGroup, clutterGroup);
const trails = new Map();          // id -> {line, pts:[Vector3]}

// range r + azimuth deg -> world position (x left/right, z depth)
function place(r, azDeg) {
  const a = azDeg * Math.PI / 180;
  return new THREE.Vector3(r * Math.sin(a), 0, r * Math.cos(a));
}

function humanMesh(strength) {
  const g = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.22, 1.3, 6, 12),
    new THREE.MeshBasicMaterial({ color: GREEN, transparent: true, opacity: 0.85 }));
  body.position.y = 0.9;
  const glow = new THREE.Mesh(
    new THREE.CylinderGeometry(0.45, 0.55, 1.9, 16, 1, true),
    new THREE.MeshBasicMaterial({ color: GREEN, transparent: true, opacity: 0.12, side: THREE.DoubleSide }));
  glow.position.y = 0.95;
  g.add(body, glow);
  return g;
}
function hardMesh() {
  return new THREE.Mesh(
    new THREE.BoxGeometry(0.5, 0.6, 0.2),
    new THREE.MeshBasicMaterial({ color: HARD, transparent: true, opacity: 0.8 }));
}
function fallingMesh() {
  return new THREE.Mesh(
    new THREE.IcosahedronGeometry(0.22, 0),
    new THREE.MeshBasicMaterial({ color: HARD }));
}

function buildTarget(t) {
  let m;
  if (t.class === "human") m = humanMesh(t.strength);
  else if (t.class === "falling") m = fallingMesh();
  else m = hardMesh();
  const p = place(t.range, t.az);
  const y = t.class === "falling" ? 1.2 : t.class === "hard" ? 0.3 : 0;
  m.position.set(p.x, y, p.z);
  // azimuth uncertainty -> wider = less certain (coarse look = feature)
  const spreadScale = 1 + (t.spread || 0) * 1.5;
  m.scale.x *= spreadScale;
  targetGroup.add(m);
  return m;
}

function updateTrail(t) {
  if (t.class !== "human") return;
  const p = place(t.range, t.az); p.y = 0.05;
  let tr = trails.get(t.id);
  if (!tr) {
    const geo = new THREE.BufferGeometry();
    const line = new THREE.Line(geo, new THREE.LineBasicMaterial({ color: GREEN, transparent: true, opacity: 0.5 }));
    tr = { line, pts: [] }; trails.set(t.id, tr); scene.add(line);
  }
  tr.pts.push(p); if (tr.pts.length > 40) tr.pts.shift();
  tr.line.geometry.setFromPoints(tr.pts);
}

// ---------- data ----------
let replay = null, ri = 0, mode = sensorUrl ? "LIVE" : "REPLAY", frame = null;

async function nextFrame() {
  if (sensorUrl) {
    try { return await (await fetch(sensorUrl + "/", { cache: "no-store" })).json(); }
    catch { mode = "LIVE (no signal)"; return null; }
  }
  if (!replay) replay = await (await fetch("./sample.json", { cache: "no-store" })).json();
  return replay.length ? replay[ri++ % replay.length] : null;
}

setInterval(async () => {
  const f = await nextFrame();
  if (f) frame = f;
}, 120);

function applyFrame(f) {
  targetGroup.clear();
  clutterGroup.clear();
  for (const c of f.clutter || []) {                    // static -> grey range arcs
    const arc = new THREE.Mesh(
      new THREE.TorusGeometry(c.range, 0.015, 6, 48, FOV * 2 * Math.PI / 180),
      new THREE.MeshBasicMaterial({ color: GREY, transparent: true, opacity: 0.25 + 0.4 * c.strength }));
    arc.rotation.x = Math.PI / 2;
    arc.rotation.z = Math.PI / 2 - FOV * Math.PI / 180;
    clutterGroup.add(arc);
  }
  const seen = new Set();
  for (const t of f.targets || []) { buildTarget(t); updateTrail(t); seen.add(t.id); }
  for (const [id, tr] of trails) if (!seen.has(id)) { scene.remove(tr.line); trails.delete(id); }
  // HUD
  document.getElementById("mode").textContent = mode;
  document.getElementById("rng").textContent = (f.max_range || 6) + "m";
  document.getElementById("cnt").textContent = (f.targets || []).length;
  document.getElementById("list").innerHTML = (f.targets || [])
    .map(t => `&gt; ${t.class.toUpperCase()} @ ${t.range}m ${t.az>0?"+":""}${t.az}° ${t.vel}m/s`).join("<br>");
}

// ---------- loop ----------
addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight); composer.setSize(innerWidth, innerHeight);
});

let last = null;
function animate() {
  requestAnimationFrame(animate);
  if (frame && frame !== last) { applyFrame(frame); last = frame; }
  controls.update();
  composer.render();
}
animate();
