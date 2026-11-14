// The Repair Core: a Three.js scene that acts out PatchPilot's loop.
// Repository modules orbit a wireframe core. A scan finds a faulty module; a packet carries
// proposal 1 to the sandbox, where it is rejected; feedback returns; proposal 2 passes the
// sandbox and goes on to the evaluator; the module turns green. Loaded only on capable devices.
import * as THREE from 'three';

const C = {
  signal: new THREE.Color('#5ee6d0'), base: new THREE.Color('#2b3a4a'), scan: new THREE.Color('#5b7d93'),
  bad: new THREE.Color('#ff6b62'), ok: new THREE.Color('#5ad19a'), sandbox: new THREE.Color('#f2b552'), evalc: new THREE.Color('#b49dff'),
};
const X_AXIS = new THREE.Vector3(1, 0, 0);
const CYCLE = 11; // seconds per repair story

// [start, end, from, to, status]  (times in seconds within the cycle)
const LEGS = [
  [2.0, 3.0, 'module', 'core', 'proposal 1'],
  [3.0, 4.1, 'core', 'sandbox', 'sandbox checks'],
  [4.1, 5.1, 'sandbox', 'core', 'rejected · feedback'],
  [5.4, 6.5, 'core', 'sandbox', 'proposal 2'],
  [6.5, 7.7, 'sandbox', 'eval', 'hidden tests'],
];

function dotTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.25, 'rgba(255,255,255,.6)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

export function startHero({ canvas, labels, status, lite = false, onFirstFrame, onLost }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: !lite, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, lite ? 1.25 : 1.75));
  renderer.setClearColor(0x000000, 0);
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x07090b, 12, 30);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
  camera.position.set(0, 0, 14);

  const disposables = [];
  const keep = (x) => { disposables.push(x); return x; };
  const root = new THREE.Group();
  scene.add(root);

  // the core: two counter-rotating wireframe solids and a faint solid heart
  const coreGroup = new THREE.Group();
  root.add(coreGroup);
  const lineMat = keep(new THREE.LineBasicMaterial({ color: C.signal, transparent: true, opacity: 0.75 }));
  const outer = new THREE.LineSegments(keep(new THREE.EdgesGeometry(keep(new THREE.IcosahedronGeometry(1.55, 1)))), lineMat);
  const innerMat = keep(new THREE.LineBasicMaterial({ color: C.signal, transparent: true, opacity: 0.4 }));
  const inner = new THREE.LineSegments(keep(new THREE.EdgesGeometry(keep(new THREE.OctahedronGeometry(0.85, 0)))), innerMat);
  coreGroup.add(outer, inner);

  // orbit guides
  const ringMat = keep(new THREE.LineBasicMaterial({ color: 0x2a3644, transparent: true, opacity: 0.55 }));
  const rings = [[3.3, 0.38], [4.4, -0.22]].map(([r, tilt]) => {
    const pts = new THREE.EllipseCurve(0, 0, r, r).getPoints(128).map((p) => new THREE.Vector3(p.x, 0, p.y));
    const line = new THREE.LineLoop(keep(new THREE.BufferGeometry().setFromPoints(pts)), ringMat);
    line.rotation.x = tilt;
    root.add(line);
    return { r, tilt };
  });

  // repository modules: one instanced mesh, one draw call
  const N = lite ? 22 : 34;
  const modGeo = keep(new THREE.BoxGeometry(0.46, 0.28, 0.07));
  const modMat = keep(new THREE.MeshStandardMaterial({ roughness: 0.45, metalness: 0.35 }));
  const modules = new THREE.InstancedMesh(modGeo, modMat, N);
  modules.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  root.add(modules);
  const mods = Array.from({ length: N }, (_, i) => {
    const ring = rings[i % 2];
    return { ring, a: (i / N) * Math.PI * 2 * 1.0 + (i % 2) * 0.4, color: C.base.clone(), lift: 0, pos: new THREE.Vector3() };
  });
  mods.forEach((m, i) => modules.setColorAt(i, m.color));

  // sandbox (amber cube) and evaluator (violet octahedron): separate trust zones, never merged
  const sandboxPos = new THREE.Vector3(2.3, -1.05, 0.6);
  const evalPos = new THREE.Vector3(-2.05, -0.75, 0.2);
  const sandboxMat = keep(new THREE.LineBasicMaterial({ color: C.sandbox, transparent: true, opacity: 0.85 }));
  const sandbox = new THREE.LineSegments(keep(new THREE.EdgesGeometry(keep(new THREE.BoxGeometry(0.72, 0.72, 0.72)))), sandboxMat);
  sandbox.position.copy(sandboxPos);
  const evalMat = keep(new THREE.LineBasicMaterial({ color: C.evalc, transparent: true, opacity: 0.85 }));
  const evaluator = new THREE.LineSegments(keep(new THREE.EdgesGeometry(keep(new THREE.OctahedronGeometry(0.5, 0)))), evalMat);
  evaluator.position.copy(evalPos);
  root.add(sandbox, evaluator);

  // packet: a bright point with a halo
  const tex = keep(dotTexture());
  const packetMat = keep(new THREE.PointsMaterial({ size: 0.9, map: tex, color: C.signal, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
  const packetGeo = keep(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0], 3)));
  const packet = new THREE.Points(packetGeo, packetMat);
  root.add(packet);
  // the core's heart: a soft additive glow, not a solid ball
  const heartMat = keep(new THREE.PointsMaterial({ size: 2.6, map: tex, color: C.signal, transparent: true, opacity: 0.45, depthWrite: false, blending: THREE.AdditiveBlending }));
  coreGroup.add(new THREE.Points(keep(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0], 3))), heartMat));

  // atmosphere: a sparse point field
  const P = lite ? 450 : 1300;
  const pos = new Float32Array(P * 3);
  for (let i = 0; i < P; i += 1) {
    const r = 6 + Math.random() * 10;
    const th = Math.random() * Math.PI * 2;
    const ph = Math.acos(2 * Math.random() - 1);
    pos.set([r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph) * 0.6, r * Math.sin(ph) * Math.sin(th)], i * 3);
  }
  const dustGeo = keep(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(pos, 3)));
  const dust = new THREE.Points(dustGeo, keep(new THREE.PointsMaterial({ size: 0.07, map: tex, color: 0x7fb8c4, transparent: true, opacity: 0.55, depthWrite: false, blending: THREE.AdditiveBlending })));
  scene.add(dust);

  // light: a cool fill plus a point light that follows the pointer
  scene.add(new THREE.AmbientLight(0x8fa4b8, 0.6));
  const key = new THREE.PointLight(0x5ee6d0, 40, 18, 1.6);
  key.position.set(2, 2, 5);
  scene.add(key);

  /* ---------- state ---------- */
  const clock = new THREE.Clock();
  const pointer = new THREE.Vector2(0, 0);
  const pointerPx = { x: -9999, y: -9999 };
  let width = 1;
  let height = 1;
  let scroll = 0;
  let fault = 3;
  let lastCycle = -1;
  let running = false;
  let first = true;
  const tmp = new THREE.Vector3();
  const obj = new THREE.Object3D();
  const proj = new THREE.Vector3();
  const labelEls = Object.fromEntries(Array.from(labels.querySelectorAll('[data-anchor]')).map((el) => [el.dataset.anchor, el]));
  let lastStatus = '';

  const anchors = { repo: new THREE.Vector3(1.8, 3.1, 0), sandbox: sandboxPos.clone().add(new THREE.Vector3(0.7, 0.2, 0)), eval: evalPos.clone().add(new THREE.Vector3(0.65, 0.1, 0)), status: new THREE.Vector3(0.2, 2.25, 0) };
  function layout() {
    // desktop: right of the copy; tablet and phone: a band of its own above the copy
    const [x, y, k] = width >= 1180 ? [3.7, 0.75, 0.92] : width >= 700 ? [2.4, 0.05, 1.05] : [0, 0.05, 0.95];
    root.position.set(x, y, 0);
    root.scale.setScalar(k);
    anchors.repo.set(...(width >= 700 ? [1.8, 3.1, 0] : [-3.4, 2.5, 0]));
  }
  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    width = Math.max(1, r.width);
    height = Math.max(1, r.height);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    layout();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvas.parentElement);
  resize();

  const onPointer = (e) => {
    const r = canvas.getBoundingClientRect();
    pointerPx.x = e.clientX - r.left;
    pointerPx.y = e.clientY - r.top;
    pointer.set((pointerPx.x / r.width) * 2 - 1, -(pointerPx.y / r.height) * 2 + 1);
  };
  window.addEventListener('pointermove', onPointer, { passive: true });

  function setStatus(text, kind) {
    if (text === lastStatus) return;
    lastStatus = text;
    status.textContent = text;
    status.classList.toggle('is-bad', kind === 'bad');
    status.classList.toggle('is-ok', kind === 'ok');
  }
  const pointOf = (name) => {
    if (name === 'module') return mods[fault].pos;
    if (name === 'core') return coreGroup.position;
    if (name === 'sandbox') return sandboxPos;
    return evalPos;
  };

  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05);
    const time = clock.elapsedTime;
    const t = time % CYCLE;
    const cycle = Math.floor(time / CYCLE);
    if (cycle !== lastCycle) {
      if (lastCycle >= 0) { mods[fault].color.copy(C.base); fault = (fault + 7) % N; }
      lastCycle = cycle;
    }

    // camera: pointer parallax and scroll pull-back
    camera.position.x += (pointer.x * 0.7 - camera.position.x) * 0.04;
    camera.position.y += (pointer.y * 0.45 - camera.position.y) * 0.04;
    camera.position.z += (14 + scroll * 7 - camera.position.z) * 0.08;
    camera.lookAt(root.position.x * 0.35, root.position.y * 0.35, 0);
    tmp.set(pointer.x, pointer.y, 0.6).unproject(camera);
    key.position.lerp(tmp, 0.08);

    // core
    outer.rotation.y += dt * 0.18; outer.rotation.x += dt * 0.05;
    inner.rotation.y -= dt * 0.32; inner.rotation.z += dt * 0.12;
    root.rotation.y = scroll * 0.9;
    sandbox.rotation.y += dt * 0.25; evaluator.rotation.y -= dt * 0.3;
    dust.rotation.y += dt * 0.012;

    // story beats
    let beat = ['scanning snapshot', ''];
    if (t >= 1.6) beat = ['fault located', 'bad'];
    let leg = null;
    for (const l of LEGS) if (t >= l[0] && t < l[1]) leg = l;
    if (leg) beat = [leg[4], leg[4].startsWith('rejected') ? 'bad' : ''];
    if (t >= 7.7) beat = ['resolved', 'ok'];
    setStatus(beat[0], beat[1]);
    const flash = (at) => Math.max(0, 1 - Math.abs(t - at) * 2.2);
    sandboxMat.color.copy(C.sandbox).lerp(C.bad, flash(4.1)).lerp(C.ok, flash(6.5));
    evalMat.color.copy(C.evalc).lerp(C.ok, flash(7.7));
    const corePulse = 1 + 0.12 * (flash(3.0) + flash(5.1));
    coreGroup.scale.setScalar(corePulse);
    lineMat.opacity = 0.6 + 0.3 * (flash(3.0) + flash(5.1));
    heartMat.opacity = 0.35 + 0.35 * (flash(3.0) + flash(5.1)) + 0.05 * Math.sin(time * 2);

    // modules
    const scanIdx = t < 1.6 ? Math.floor((t / 1.6) * N) : -1;
    mods.forEach((m, i) => {
      m.a += dt * (m.ring.r < 4 ? 0.09 : -0.06);
      tmp.set(Math.cos(m.a) * m.ring.r, 0, Math.sin(m.a) * m.ring.r).applyAxisAngle(X_AXIS, m.ring.tilt);
      // pointer proximity lifts a module away from the core
      proj.copy(tmp).applyMatrix4(root.matrixWorld).project(camera);
      const sx = (proj.x * 0.5 + 0.5) * width;
      const sy = (-proj.y * 0.5 + 0.5) * height;
      const near = Math.max(0, 1 - Math.hypot(sx - pointerPx.x, sy - pointerPx.y) / 140);
      m.lift += (near - m.lift) * 0.12;
      m.pos.copy(tmp).multiplyScalar(1 + m.lift * 0.08);
      obj.position.copy(m.pos);
      obj.lookAt(0, 0, 0);
      obj.scale.setScalar(1 + m.lift * 0.45);
      obj.updateMatrix();
      modules.setMatrixAt(i, obj.matrix);
      let target = C.base;
      if (i === fault && t >= 1.6) target = t >= 7.7 ? C.ok : C.bad;
      else if (i === scanIdx || i === scanIdx - 1) target = C.scan;
      m.color.lerp(target, 0.15);
      if (m.lift > 0.05) tmp.copy(m.color).lerp(C.signal, m.lift * 0.5), modules.setColorAt(i, tmp);
      else modules.setColorAt(i, m.color);
    });
    modules.instanceMatrix.needsUpdate = true;
    modules.instanceColor.needsUpdate = true;

    // packet along the active leg
    if (leg) {
      const k = (t - leg[0]) / (leg[1] - leg[0]);
      const e = k < 0.5 ? 2 * k * k : 1 - ((-2 * k + 2) ** 2) / 2;
      const a = pointOf(leg[2]);
      const b = pointOf(leg[3]);
      const mid = tmp.copy(a).add(b).multiplyScalar(0.5);
      mid.z += 1.4;
      const p = new THREE.QuadraticBezierCurve3(a.clone(), mid.clone(), b.clone()).getPoint(e);
      packetGeo.attributes.position.setXYZ(0, p.x, p.y, p.z);
      packetGeo.attributes.position.needsUpdate = true;
      packetMat.color.copy(leg[4].startsWith('rejected') ? C.bad : C.signal);
      packetMat.opacity = 1;
    } else {
      packetMat.opacity = Math.max(0, packetMat.opacity - dt * 3);
    }

    renderer.render(scene, camera);
    root.updateMatrixWorld();

    // HTML labels follow their 3D anchors
    for (const [name, el] of Object.entries(labelEls)) {
      tmp.copy(anchors[name]).applyMatrix4(root.matrixWorld).project(camera);
      const x = (tmp.x * 0.5 + 0.5) * width;
      const y = (-tmp.y * 0.5 + 0.5) * height;
      el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
      el.style.opacity = tmp.z < 1 && x > 0 && x < width - 40 ? String(Math.max(0, 1 - scroll * 2.5)) : '0';
    }
    if (first) { first = false; onFirstFrame?.(); }
  }

  function setRunning(on) {
    if (on === running) return;
    running = on;
    if (on) clock.getDelta();
    renderer.setAnimationLoop(on ? frame : null);
  }
  let inView = true;
  const io = new IntersectionObserver(([e]) => { inView = e.isIntersecting; setRunning(inView && !document.hidden); });
  io.observe(canvas);
  const onVis = () => setRunning(inView && !document.hidden);
  document.addEventListener('visibilitychange', onVis);
  canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); setRunning(false); onLost?.(); }, { once: true });
  setRunning(true);

  return {
    setScroll(p) { scroll = p; },
    dispose() {
      setRunning(false);
      io.disconnect();
      ro.disconnect();
      window.removeEventListener('pointermove', onPointer);
      document.removeEventListener('visibilitychange', onVis);
      modules.dispose();
      disposables.forEach((d) => d.dispose());
      renderer.dispose();
    },
  };
}
