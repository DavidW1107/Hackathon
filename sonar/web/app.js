import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

const GREEN = 0x39ff88, HARD = 0xb8c2c9, SOFT = 0x6f7a74;
const CEIL = 2.4, DOOR_H = 2.0, DOOR_W = 0.9;    // standard building dimensions (m)
const params = new URLSearchParams(location.search);
// live by default when viewing locally; a deployed (https) page falls back to replay.
const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);
const sensorUrl = params.get("sensor") || (!params.has("demo") && isLocal ? "http://localhost:8765" : null);

// live-tunable render + detection knobs, persisted across reloads
const CFG = Object.assign({ bloom: 0.8, human: 1.0, arch: 1.0, smooth: 0.15, trail: 40, motion: 0.15 },
  JSON.parse(localStorage.getItem("voidcfg") || "{}"));

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

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.8, 0.5, 0.35);
composer.addPass(bloom);

const grid = new THREE.GridHelper(16, 32, 0x1c3a2b, 0x0f1f18);
grid.position.z = 4;
scene.add(grid);

// faint ceiling grid at standard height -> the void reads as a room with scale
const ceiling = new THREE.GridHelper(16, 16, 0x123a2a, 0x0c1712);
ceiling.position.set(0, CEIL, 4);
ceiling.material.transparent = true; ceiling.material.opacity = 0.12;
scene.add(ceiling);

const node = new THREE.Mesh(new THREE.SphereGeometry(0.12, 16, 16),
  new THREE.MeshBasicMaterial({ color: GREEN }));
scene.add(node);

const FOV = 50;
const wedge = new THREE.Group();
for (const s of [-1, 1]) {
  const a = s * FOV * Math.PI / 180;
  wedge.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(
    [new THREE.Vector3(0, 0.02, 0), new THREE.Vector3(Math.sin(a) * 6, 0.02, Math.cos(a) * 6)]),
    new THREE.LineBasicMaterial({ color: 0x1f8a52, transparent: true, opacity: 0.5 })));
}
scene.add(wedge);

const clutterGroup = new THREE.Group();
scene.add(clutterGroup);
const clock = new THREE.Clock();

// range r + azimuth deg -> world position (x = left/right, z = depth)
function place(r, azDeg) {
  const a = (azDeg || 0) * Math.PI / 180;
  return new THREE.Vector3(r * Math.sin(a), 0, r * Math.cos(a));
}

// ---------- animated wireframe human ----------
function makeHuman() {
  const g = new THREE.Group();
  const mat = new THREE.MeshBasicMaterial({ color: GREEN });
  const bone = (len, r = 0.045) => {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 8), mat);
    m.position.y = -len / 2; return m;                 // hang from a pivot at the top
  };
  const limb = (x, y, len) => { const p = new THREE.Group(); p.position.set(x, y, 0); p.add(bone(len)); return p; };
  const torso = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.08, 0.62, 10), mat); torso.position.y = 1.13;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.13, 16, 12), mat); head.position.y = 1.58;
  const lArm = limb(-0.17, 1.4, 0.55), rArm = limb(0.17, 1.4, 0.55);
  const lLeg = limb(-0.09, 0.82, 0.82), rLeg = limb(0.09, 0.82, 0.82);
  const aura = new THREE.Mesh(new THREE.CylinderGeometry(0.4, 0.5, 1.9, 16, 1, true),
    new THREE.MeshBasicMaterial({ color: GREEN, transparent: true, opacity: 0.07, side: THREE.DoubleSide }));
  aura.position.y = 0.95;
  g.add(torso, head, lArm, rArm, lLeg, rLeg, aura);
  let phase = 0;
  return {
    group: g,
    update(dt, speed) {                                // walk cycle, faster/wider with speed
      phase += dt * (3 + speed * 4);
      const amp = Math.min(0.25 + speed * 0.6, 1.1);
      lArm.rotation.x = Math.sin(phase) * amp; rArm.rotation.x = -Math.sin(phase) * amp;
      lLeg.rotation.x = -Math.sin(phase) * amp; rLeg.rotation.x = Math.sin(phase) * amp;
    },
  };
}
const hardMesh = () => new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.6, 0.2),
  new THREE.MeshBasicMaterial({ color: SOFT, transparent: true, opacity: 0.55 }));
const fallingMesh = () => new THREE.Mesh(new THREE.IcosahedronGeometry(0.22, 0),
  new THREE.MeshBasicMaterial({ color: HARD }));

// ---------- snap static reflectors to standard architectural elements ----------
// ponytail: strength -> type is a heuristic (sonar can't truly separate wall/door/
// sofa); the DIMENSIONS are standard building sizes so proportions read true.
function makeWall() {
  const m = new THREE.Mesh(new THREE.BoxGeometry(1.2, CEIL, 0.08),
    new THREE.MeshBasicMaterial({ color: HARD, transparent: true, opacity: 0.13 }));
  m.userData.y = CEIL / 2; return m;
}
function makeDoor() {                                  // 2.0m x 0.9m frame (6'6" doorway)
  const g = new THREE.Group();
  const fm = new THREE.MeshBasicMaterial({ color: HARD, transparent: true, opacity: 0.5 });
  const th = 0.06;
  const bar = (w, h, x, y) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, th), fm); m.position.set(x, y, 0); return m; };
  g.add(bar(DOOR_W + th, th, 0, DOOR_H),               // lintel + two posts
        bar(th, DOOR_H, -DOOR_W / 2, DOOR_H / 2), bar(th, DOOR_H, DOOR_W / 2, DOOR_H / 2));
  const panel = new THREE.Mesh(new THREE.BoxGeometry(DOOR_W, DOOR_H, 0.02),
    new THREE.MeshBasicMaterial({ color: HARD, transparent: true, opacity: 0.07 }));
  panel.position.y = DOOR_H / 2; g.add(panel);
  g.userData.y = 0; return g;
}
function makeCouch() {                                 // soft furniture ~1.8 x 0.8 x 0.9m
  const m = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.8, 0.9),
    new THREE.MeshBasicMaterial({ color: SOFT, transparent: true, opacity: 0.28 }));
  m.userData.y = 0.4; return m;
}
function buildClutter(f) {
  clutterGroup.clear();
  for (const c of f.clutter || []) {
    const p = place(c.range, c.az);
    const m = c.strength > 0.7 ? makeWall() : c.strength > 0.45 ? makeDoor() : makeCouch();
    m.position.set(p.x, m.userData.y, p.z);
    m.lookAt(0, m.userData.y, 0);                      // face the sensor
    m.traverse(o => { if (o.material) o.material.opacity *= CFG.arch; });
    clutterGroup.add(m);
  }
}

// ---------- lightweight tracker (nearest-neighbour match + lerp) ----------
// ponytail: NN by position + a lerp. No Kalman; add if targets swap identity.
const tracked = [];
function nearest(pos) {
  let best = null, bd = 1.2;
  for (const t of tracked) { const d = t.pos.distanceTo(pos); if (d < bd) { bd = d; best = t; } }
  return best;
}
function addTracked(cls, pos) {
  let mesh, human = null, trail = null, trailPts = null;
  if (cls === "human") {
    const h = makeHuman(); mesh = h.group; human = h;
    trailPts = [];
    trail = new THREE.Line(new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: GREEN, transparent: true, opacity: 0.4 }));
    scene.add(trail);
  } else mesh = cls === "falling" ? fallingMesh() : hardMesh();
  mesh.position.copy(pos); scene.add(mesh);
  const t = { mesh, human, cls, pos: pos.clone(), tgt: pos.clone(), vel: 0, seen: true, trail, trailPts };
  tracked.push(t); return t;
}
function removeTracked(t) { scene.remove(t.mesh); if (t.trail) scene.remove(t.trail); }
function applyTargets(f) {
  for (const t of tracked) t.seen = false;
  for (const d of f.targets || []) {
    const pos = place(d.range, d.az); pos.y = d.class === "falling" ? 1.2 : 0;
    let t = nearest(pos);
    if (t && t.cls !== d.class) { removeTracked(t); tracked.splice(tracked.indexOf(t), 1); t = null; }
    if (!t) t = addTracked(d.class, pos);
    t.tgt.copy(pos); t.vel = d.vel; t.seen = true;
  }
  for (let i = tracked.length - 1; i >= 0; i--)
    if (!tracked[i].seen) { removeTracked(tracked[i]); tracked.splice(i, 1); }
}

// ---------- data ----------
let replay = null, ri = 0, mode = sensorUrl ? "LIVE" : "REPLAY", frame = null, last = null, liveFail = 0;
async function nextFrame() {
  if (sensorUrl && liveFail < 5) {                 // try live; fall back to replay after ~0.5s
    try {
      const f = await (await fetch(sensorUrl + "/", { cache: "no-store" })).json();
      liveFail = 0; mode = "LIVE"; return f;
    } catch { if (++liveFail >= 5) mode = "REPLAY (no sensor)"; return null; }
  }
  if (!replay) replay = await (await fetch("./sample.json", { cache: "no-store" })).json();
  return replay.length ? replay[ri++ % replay.length] : null;
}
setInterval(async () => { const f = await nextFrame(); if (f) frame = f; }, 100);

function updateHUD(f) {
  document.getElementById("mode").textContent = mode;
  document.getElementById("rng").textContent = (f.max_range || 6) + "m";
  document.getElementById("cnt").textContent = (f.targets || []).length;
  document.getElementById("list").innerHTML = (f.targets || []).map(t =>
    `&gt; ${t.class.toUpperCase()} @ ${t.range}m ${t.az > 0 ? "+" : ""}${t.az}° ${t.vel}m/s`).join("<br>");
}

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight); composer.setSize(innerWidth, innerHeight);
});

// ---------- live tuning panel (native sliders, no dep) ----------
const CTRLS = [
  ["bloom", 0, 2, 0.05, "bloom glow"],
  ["human", 0.5, 2, 0.05, "human size"],
  ["arch", 0, 1, 0.05, "structure opacity"],
  ["smooth", 0.03, 0.5, 0.01, "motion smoothing"],
  ["trail", 0, 120, 5, "trail length"],
  ["motion", 0.05, 0.4, 0.01, "sensitivity (live)"],
];
function applyCfg(k) {
  if (k === "bloom") bloom.strength = CFG.bloom;
  if (k === "motion" && sensorUrl) fetch(sensorUrl + "/config?motion=" + CFG.motion).catch(() => {});
}
(function panel() {
  const el = document.createElement("div"); el.id = "panel";
  el.innerHTML = "<b>TUNE</b>" + CTRLS.map(([k, mn, mx, st, lb]) =>
    `<label>${lb}<span id="v_${k}">${CFG[k]}</span></label>` +
    `<input id="c_${k}" type="range" min="${mn}" max="${mx}" step="${st}" value="${CFG[k]}">`).join("");
  document.body.appendChild(el);
  for (const [k] of CTRLS) {
    document.getElementById("c_" + k).oninput = (e) => {
      CFG[k] = parseFloat(e.target.value);
      document.getElementById("v_" + k).textContent = CFG[k];
      localStorage.setItem("voidcfg", JSON.stringify(CFG));
      applyCfg(k);
    };
  }
  bloom.strength = CFG.bloom;
  applyCfg("motion");
})();

// ---------- render loop (60fps; smooths between ~10Hz detection frames) ----------
function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.05);
  if (frame && frame !== last) { buildClutter(frame); applyTargets(frame); updateHUD(frame); last = frame; }
  for (const t of tracked) {
    const dir = t.tgt.clone().sub(t.pos);
    t.pos.lerp(t.tgt, CFG.smooth); t.mesh.position.copy(t.pos);
    if (t.human) {
      t.mesh.scale.setScalar(CFG.human);
      t.human.update(dt, t.vel);
      if (dir.length() > 0.03) t.mesh.rotation.y = Math.atan2(dir.x, dir.z);
      const p = t.pos.clone(); p.y = 0.05;
      t.trailPts.push(p); while (t.trailPts.length > CFG.trail) t.trailPts.shift();
      t.trail.geometry.setFromPoints(t.trailPts);
    }
  }
  controls.update();
  composer.render();
}
animate();
