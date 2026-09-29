/* Phoenix 3D — effects: chimney smoke, torch fire, fountain spray, footstep dust, ore
   glints; and wildlife that comes and goes with the hour (birds by day, butterflies in the
   sun, fireflies at night). All sprites are drawn by one small shader; nothing is fetched. */
import * as THREE from 'three';
import { srand, clamp, lerp, glowTexture } from './util.js';

const VERT = `
  attribute vec4 aColor; attribute float aSize; uniform float uScale;
  varying vec4 vC;
  void main() {
    vC = aColor; vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = clamp(aSize * uScale / max(0.1, -mv.z), 0.0, 220.0);
    gl_Position = projectionMatrix * mv;
  }`;
const FRAG = `
  varying vec4 vC;
  void main() {
    vec2 c = gl_PointCoord - 0.5; float d = length(c) * 2.0;
    float a = smoothstep(1.0, 0.0, d); a *= a;
    gl_FragColor = vec4(vC.rgb, vC.a * a);
  }`;

export class Particles {
  constructor(max, { additive = false } = {}) {
    this.max = max; this.cursor = 0;
    this.pos = new Float32Array(max * 3); this.vel = new Float32Array(max * 3);
    this.col = new Float32Array(max * 4); this.size = new Float32Array(max);
    this.life = new Float32Array(max); this.age = new Float32Array(max);
    this.grow = new Float32Array(max); this.drag = new Float32Array(max); this.grav = new Float32Array(max);
    this.a0 = new Float32Array(max); this.s0 = new Float32Array(max);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aColor', new THREE.BufferAttribute(this.col, 4).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aSize', new THREE.BufferAttribute(this.size, 1).setUsage(THREE.DynamicDrawUsage));
    this.material = new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false,
      blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending, uniforms: { uScale: { value: 600 } } });
    this.points = new THREE.Points(g, this.material); this.points.frustumCulled = false; this.points.userData.noAO = true;
    this.points.renderOrder = 5;
  }
  spawn(x, y, z, vx, vy, vz, life, size, grow, r, g, b, a, drag = 0.4, grav = 0) {
    const i = this.cursor; this.cursor = (this.cursor + 1) % this.max;
    this.pos[i * 3] = x; this.pos[i * 3 + 1] = y; this.pos[i * 3 + 2] = z;
    this.vel[i * 3] = vx; this.vel[i * 3 + 1] = vy; this.vel[i * 3 + 2] = vz;
    this.life[i] = life; this.age[i] = 0; this.s0[i] = size; this.grow[i] = grow; this.a0[i] = a;
    this.col[i * 4] = r; this.col[i * 4 + 1] = g; this.col[i * 4 + 2] = b; this.drag[i] = drag; this.grav[i] = grav;
  }
  update(dt) {
    let live = 0;
    for (let i = 0; i < this.max; i++) {
      if (this.life[i] <= 0) { this.col[i * 4 + 3] = 0; continue; }
      this.age[i] += dt; const k = this.age[i] / this.life[i];
      if (k >= 1) { this.life[i] = 0; this.col[i * 4 + 3] = 0; continue; }
      const dr = Math.max(0, 1 - this.drag[i] * dt);
      this.vel[i * 3] *= dr; this.vel[i * 3 + 2] *= dr; this.vel[i * 3 + 1] = this.vel[i * 3 + 1] * dr - this.grav[i] * dt;
      this.pos[i * 3] += this.vel[i * 3] * dt; this.pos[i * 3 + 1] += this.vel[i * 3 + 1] * dt; this.pos[i * 3 + 2] += this.vel[i * 3 + 2] * dt;
      this.size[i] = this.s0[i] * (1 + this.grow[i] * k);
      this.col[i * 4 + 3] = this.a0[i] * Math.min(1, k * 8) * (1 - k) * (1 - k * 0.3);
      live++;
    }
    this.points.geometry.attributes.position.needsUpdate = true;
    this.points.geometry.attributes.aColor.needsUpdate = true;
    this.points.geometry.attributes.aSize.needsUpdate = true;
    return live;
  }
}

export class Fx {
  constructor(scene, renderer, camera) {
    this.renderer = renderer; this.camera = camera;
    this.smoke = new Particles(700); this.glow = new Particles(1400, { additive: true });
    scene.add(this.smoke.points, this.glow.points);
    this.emitters = []; this.tmp = new THREE.Vector3(); this.rnd = srand(31337); this.light = 1; this.tint = new THREE.Color(1, 1, 1);
    this.wind = new THREE.Vector3(0.22, 0.05, 0.08);
  }
  addEmitter(owner, kind, parent, x, y, z, opts = {}) { this.emitters.push({ owner, kind, parent, x, y, z, acc: this.rnd() * 3, opts }); }
  removeOwner(owner) { this.emitters = this.emitters.filter((e) => e.owner !== owner); }
  dust(x, y, z, tone = 0.55) {
    const r = this.rnd;
    for (let i = 0; i < 2; i++) this.smoke.spawn(x + (r() - 0.5) * 0.04, y + 0.02, z + (r() - 0.5) * 0.04, (r() - 0.5) * 0.12, 0.06 + r() * 0.05, (r() - 0.5) * 0.12,
      0.7 + r() * 0.4, 0.05, 2.4, tone * this.tint.r, tone * this.tint.g * 0.92, tone * this.tint.b * 0.8, 0.32, 1.4);
  }
  burst(x, y, z, hex, n = 14) {                     // a celebratory puff of light (adoption, advance)
    const c = new THREE.Color(hex), r = this.rnd;
    for (let i = 0; i < n; i++) { const a = r() * 6.28, s = 0.3 + r() * 0.6;
      this.glow.spawn(x, y, z, Math.cos(a) * s, 0.5 + r() * 0.9, Math.sin(a) * s, 0.9 + r() * 0.8, 0.07, -0.3, c.r, c.g, c.b, 0.95, 1.2, 0.8); }
  }
  update(dt, t, sky) {
    const cam = this.camera.position, r = this.rnd, tmp = this.tmp;
    const day = sky ? sky.day : 1, night = sky ? sky.night : 0;
    this.smoke.material.uniforms.uScale.value = this.glow.material.uniforms.uScale.value =
      this.renderer.domElement.height * 0.5 / Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2);
    this.tint.setRGB(0.55 + 0.45 * day, 0.55 + 0.4 * day, 0.62 + 0.3 * day).lerp(new THREE.Color(1, 0.7, 0.45), (sky?.dusk ?? 0) * 0.5);
    for (const e of this.emitters) {
      if (!e.parent.visible) continue;
      e.parent.updateWorldMatrix(true, false); tmp.set(e.x, e.y, e.z); e.parent.localToWorld(tmp);
      if (tmp.distanceToSquared(cam) > 42 * 42) continue;
      const rate = e.kind === 'smoke' ? 3.2 : e.kind === 'fire' ? 16 : e.kind === 'spray' ? 46 : e.kind === 'sparkle' ? 3 : 4;
      e.acc += dt * rate;
      while (e.acc >= 1) {
        e.acc -= 1;
        if (e.kind === 'smoke') {
          const k = 0.5 * this.tint.r; this.smoke.spawn(tmp.x, tmp.y, tmp.z, this.wind.x + (r() - 0.5) * 0.05, 0.22 + r() * 0.08, this.wind.z + (r() - 0.5) * 0.05,
            4.2 + r() * 1.5, 0.07, 4.5, k, k * 0.98, k * 0.95, 0.38, 0.08);
        } else if (e.kind === 'fire') {
          this.glow.spawn(tmp.x + (r() - 0.5) * 0.01, tmp.y, tmp.z + (r() - 0.5) * 0.01, (r() - 0.5) * 0.03, 0.14 + r() * 0.12, (r() - 0.5) * 0.03,
            0.5 + r() * 0.3, 0.075, -0.6, 1, 0.55 + r() * 0.2, 0.15, 0.7, 0.5);
          if (r() < 0.12) this.glow.spawn(tmp.x, tmp.y, tmp.z, (r() - 0.5) * 0.2, 0.3 + r() * 0.2, (r() - 0.5) * 0.2, 1.1 + r(), 0.02, -0.2, 1, 0.7, 0.3, 0.9, 0.3, -0.05);
        } else if (e.kind === 'spray') {
          const a = r() * 6.28, s = 0.05 + r() * 0.09;
          this.glow.spawn(tmp.x, tmp.y, tmp.z, Math.cos(a) * s, 0.5 + r() * 0.25, Math.sin(a) * s, 0.9, 0.035, -0.2, 0.62, 0.82, 1, 0.55, 0.05, 1.3);
        } else if (e.kind === 'sparkle') {
          const c = new THREE.Color(e.opts.color ?? 0xffe08a);
          this.glow.spawn(tmp.x + (r() - 0.5) * 0.3, tmp.y + r() * 0.25, tmp.z + (r() - 0.5) * 0.3, 0, 0.03, 0, 0.6 + r() * 0.6, 0.05, -0.5, c.r, c.g, c.b, 0.9, 0.2);
        }
      }
    }
    this.smoke.update(dt); this.glow.update(dt);
  }
}

/* ── wildlife ───────────────────────────────────────────────────────────────────── */
export class Wildlife {
  constructor(scene, field, quality = 2) {
    this.field = field; this.group = new THREE.Group(); scene.add(this.group); this.rnd = srand(2718);
    const bm = new THREE.MeshStandardMaterial({ color: 0x1c1712, roughness: 0.9, side: THREE.DoubleSide });
    this.birds = [];
    for (let i = 0; i < [4, 7, 11][quality]; i++) {
      const r = this.rnd, g = new THREE.Group();
      const body = new THREE.Mesh(new THREE.ConeGeometry(0.05, 0.28, 6), bm); body.rotation.x = Math.PI / 2; g.add(body);
      const wing = (s) => { const w = new THREE.Group(); const m = new THREE.Mesh(new THREE.PlaneGeometry(0.34, 0.12), bm);
        m.rotation.x = -Math.PI / 2; m.position.x = s * 0.17; w.add(m); g.add(w); return w; };
      g.userData = { w1: wing(1), w2: wing(-1), r: 5 + r() * 9, h: 6 + r() * 5, sp: (0.16 + r() * 0.16) * (r() < 0.5 ? -1 : 1), ph: r() * 6.28, cx: (r() - 0.5) * 8, cz: (r() - 0.5) * 6 };
      g.scale.setScalar(1.3); this.birds.push(g); this.group.add(g);
    }
    // butterflies: two wing planes beating over the flower drifts
    this.flies = [];
    const bc = [0xffd24a, 0xff8ab0, 0xa8c8ff, 0xffffff];
    for (let i = 0; i < [6, 12, 20][quality]; i++) {
      const r = this.rnd, g = new THREE.Group(), m = new THREE.MeshBasicMaterial({ color: bc[i % 4], side: THREE.DoubleSide });
      const wing = (s) => { const w = new THREE.Group(); const p = new THREE.Mesh(new THREE.CircleGeometry(0.05, 6), m); p.rotation.x = -Math.PI / 2; p.position.x = s * 0.04; w.add(p); g.add(w); return w; };
      g.userData = { w1: wing(1), w2: wing(-1), cx: (r() - 0.5) * 22, cz: (r() - 0.5) * 14, ph: r() * 6.28, sp: 0.25 + r() * 0.3, rad: 0.8 + r() * 1.6 };
      this.flies.push(g); this.group.add(g);
    }
    // fireflies: additive motes that wander at night
    const n = [40, 90, 160][quality]; this.ff = new Particles(n, { additive: true });
    this.ffData = Array.from({ length: n }, () => { const r = this.rnd; return { x: (r() - 0.5) * 26, z: (r() - 0.5) * 18, ph: r() * 6.28, sp: 0.3 + r() * 0.5, y: 0.2 + r() * 1.3 }; });
    scene.add(this.ff.points);
  }
  update(dt, t, sky, camera, uScale) {
    const day = sky.day, night = sky.night, F = this.field;
    for (const b of this.birds) { const u = b.userData, a = t * u.sp + u.ph;
      b.visible = day > 0.08;
      b.position.set(u.cx + Math.cos(a) * u.r, u.h + Math.sin(t * 0.9 + u.ph) * 0.5, u.cz + Math.sin(a) * u.r);
      b.rotation.y = -a - (u.sp > 0 ? Math.PI / 2 : -Math.PI / 2);
      const fl = Math.sin(t * 9 + u.ph) * 0.6; u.w1.rotation.z = fl; u.w2.rotation.z = -fl; }
    for (const f of this.flies) { const u = f.userData, a = t * u.sp + u.ph;
      f.visible = day > 0.5;
      const x = u.cx + Math.cos(a * 1.3) * u.rad + Math.sin(a * 2.7) * 0.4, z = u.cz + Math.sin(a) * u.rad;
      f.position.set(x, Math.max(F.heightAt(x, z), -0.05) + 0.35 + Math.sin(a * 3.1) * 0.15, z);
      f.rotation.y = -a; const fl = Math.abs(Math.sin(t * 14 + u.ph)) * 1.1; u.w1.rotation.z = fl; u.w2.rotation.z = -fl; }
    // fireflies: re-spawn each frame as a soft constellation (cheap; no per-particle life needed)
    const p = this.ff; p.uScale = uScale; p.material.uniforms.uScale.value = uScale;
    const g = clamp(night * 1.4 - 0.2);
    for (let i = 0; i < this.ffData.length; i++) {
      const d = this.ffData[i], x = d.x + Math.sin(t * d.sp + d.ph) * 1.4, z = d.z + Math.cos(t * d.sp * 0.8 + d.ph * 2) * 1.4;
      const y = Math.max(F.heightAt(x, z), -0.05) + d.y + Math.sin(t * d.sp * 1.7 + d.ph) * 0.2, blink = Math.pow(Math.max(0, Math.sin(t * 1.6 + d.ph * 3)), 2);
      p.pos[i * 3] = x; p.pos[i * 3 + 1] = y; p.pos[i * 3 + 2] = z; p.size[i] = 0.09;
      p.col[i * 4] = 0.75; p.col[i * 4 + 1] = 1; p.col[i * 4 + 2] = 0.3; p.col[i * 4 + 3] = g * (0.15 + 0.85 * blink);
    }
    p.points.geometry.attributes.position.needsUpdate = p.points.geometry.attributes.aColor.needsUpdate = p.points.geometry.attributes.aSize.needsUpdate = true;
  }
}
export { glowTexture };
