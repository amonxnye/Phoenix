/* Phoenix 3D — the building kit: cached PBR materials, textured geometry with correct UV
   scale, gable roofs, windows that light at dusk, torches, banners. Buildings and civic
   works are assembled from these, so a new building costs a few lines and no new assets. */
import * as THREE from 'three';
import { clamp, srand, glowTexture } from './util.js';

/** condition < 100 → desaturate and darken toward dust (a neglected building looks it) */
export function weather(c, cond) {
  const g = (c.r + c.g + c.b) / 3, k = cond / 100, m = 0.4 + 0.6 * k;
  return new THREE.Color(c.r * k + g * (1 - k), c.g * k + g * (1 - k), c.b * k + g * (1 - k)).multiplyScalar(m);
}

export function makeKit(T) {
  const cache = new Map(), V2 = (n) => new THREE.Vector2(n, n);
  const kit = {
    T,
    /** a textured PBR material tinted `hex`, weathered by condition */
    tex(name, hex, cond = 100, o = {}) {
      const q = Math.round(cond / 12) * 12, key = `${name}|${hex}|${q}|${o.ns ?? 1}|${o.metal ?? 0}|${o.rough ?? ''}|${o.em ?? ''}|${o.side ?? ''}`;
      let m = cache.get(key); if (m) return m;
      const t = T[name];
      m = new THREE.MeshStandardMaterial({ color: weather(new THREE.Color(hex), q), map: t.map, normalMap: t.normalMap,
        normalScale: V2(o.ns ?? 1), roughnessMap: t.roughnessMap, roughness: o.rough ?? 1, metalness: o.metal ?? 0,
        emissive: o.em ? new THREE.Color(o.em) : 0x000000, emissiveIntensity: o.em ? 0.8 : 0, side: o.side ?? THREE.FrontSide });
      cache.set(key, m); return m;
    },
    plain(hex, cond = 100, o = {}) {
      const q = Math.round(cond / 12) * 12, key = `p|${hex}|${q}|${o.rough ?? ''}|${o.metal ?? ''}|${o.em ?? ''}|${o.ei ?? ''}|${o.side ?? ''}|${o.tr ?? ''}`;
      let m = cache.get(key); if (m) return m;
      m = new THREE.MeshStandardMaterial({ color: weather(new THREE.Color(hex), q), roughness: o.rough ?? 0.8, metalness: o.metal ?? 0,
        emissive: o.em ? new THREE.Color(o.em) : 0x000000, emissiveIntensity: o.ei ?? (o.em ? 1 : 0), side: o.side ?? THREE.FrontSide,
        transparent: !!o.tr, opacity: o.tr ?? 1 });
      cache.set(key, m); return m;
    },
    /** cloth (banners, sails): double-sided, matte */
    cloth(hex) { return kit.plain(hex, 100, { rough: 0.9, side: THREE.DoubleSide }); },
    /** shared lit materials — one uniform change lights every window in the world at dusk */
    window: new THREE.MeshStandardMaterial({ color: 0x1c1a16, emissive: 0xffb45a, emissiveIntensity: 0.15, roughness: 0.2, metalness: 0.2 }),
    lantern: new THREE.MeshStandardMaterial({ color: 0x3a2c18, emissive: 0xffa640, emissiveIntensity: 0.6, roughness: 0.4 }),
    flame: new THREE.MeshBasicMaterial({ color: 0xffa640, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false }),
    wood: null, stone: null,
    banners: [],                                 // cloth planes waving in the wind
    lights: { budget: 6, list: [] },              // real point lights are precious; lamps borrow from here
  };
  kit.wood = kit.tex('planks', 0x9a7550); kit.stone = kit.tex('stone', 0xb0a696);
  kit.flameTex = glowTexture('rgba(255,214,130,1)', 'rgba(255,110,30,0)', 64);
  /** dusk lighting for every shared emissive at once */
  kit.setNight = (night, dusk) => {
    kit.window.emissiveIntensity = 0.1 + 1.5 * Math.max(night, dusk * 0.5);
    kit.lantern.emissiveIntensity = 0.4 + 1.6 * Math.max(night, dusk * 0.6);
    kit.flame.opacity = 0.75 + 0.25 * night;
  };
  return kit;
}

/* ── geometry with texture scale that matches its size ───────────────────────────── */
const UVK = 1.6;                                  // texture repeats per world unit
export function uvk(geo, su, sv) {
  const uv = geo.attributes.uv; for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * su, uv.getY(i) * sv);
  return geo;
}
/** a box whose every face carries the texture at true size */
export function texBox(w, h, d, k = UVK) {
  const g = new THREE.BoxGeometry(w, h, d), uv = g.attributes.uv, dims = [[d, h], [d, h], [w, d], [w, d], [w, h], [w, h]];
  for (let f = 0; f < 6; f++) for (let i = 0; i < 4; i++) { const j = f * 4 + i; uv.setXY(j, uv.getX(j) * dims[f][0] * k, uv.getY(j) * dims[f][1] * k); }
  return g;
}
export function texCyl(rt, rb, h, seg = 10, k = UVK) {
  const g = new THREE.CylinderGeometry(rt, rb, h, seg, 1); return uvk(g, Math.PI * (rt + rb) * k, h * k);
}
/** a flat disc/cylinder: the side wraps the texture, the caps carry it at true scale (no stretched planks) */
export function texDisc(rt, rb, h, seg = 24, k = UVK) {
  const g = new THREE.CylinderGeometry(rt, rb, h, seg, 1), uv = g.attributes.uv, ix = g.index, r = Math.max(rt, rb);
  g.groups.forEach((gr, gi) => {
    const seen = new Set();
    for (let i = gr.start; i < gr.start + gr.count; i++) { const v = ix.getX(i); if (seen.has(v)) continue; seen.add(v);
      if (gi === 0) uv.setXY(v, uv.getX(v) * Math.PI * 2 * r * k, uv.getY(v) * h * k); else uv.setXY(v, (uv.getX(v) - 0.5) * 2 * r * k, (uv.getY(v) - 0.5) * 2 * r * k); }
  });
  return g;
}
export function texCone(r, h, seg = 8, k = UVK) {
  const g = new THREE.ConeGeometry(r, h, seg, 1); return uvk(g, Math.PI * r * k * 1.5, Math.hypot(r, h) * k);
}
/** a gabled roof, ridge along x, eaves at z = ±d/2 (with underside so it is solid from below) */
export function gableRoof(w, d, h, k = UVK) {
  const FL = [-w / 2, 0, d / 2], FR = [w / 2, 0, d / 2], BL = [-w / 2, 0, -d / 2], BR = [w / 2, 0, -d / 2], RL = [-w / 2, h, 0], RR = [w / 2, h, 0];
  const pos = [], uv = [], slope = Math.hypot(d / 2, h) * k, ww = w * k;
  const quad = (a, b, c, e, u, v) => { pos.push(...a, ...b, ...c, ...a, ...c, ...e); uv.push(0, 0, u, 0, u, v, 0, 0, u, v, 0, v); };
  quad(FL, FR, RR, RL, ww, slope);            // front slope
  quad(BR, BL, RL, RR, ww, slope);            // back slope
  quad(BL, BR, FR, FL, ww, d * k);            // underside
  const tri = (a, b, c) => { pos.push(...a, ...b, ...c); uv.push(0, 0, 1 * d * k, 0, 0.5 * d * k, h * k); };
  tri(FL, BL, RL); tri(BR, FR, RR);           // gable ends
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pos), 3));
  g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(uv), 2)); g.computeVertexNormals();
  return g;
}
/** a hipped (pyramid) roof over a rectangle */
export function hipRoof(w, d, h, k = UVK) {
  const g = new THREE.ConeGeometry(0.5, 1, 4, 1); g.rotateY(Math.PI / 4); g.scale(w * Math.SQRT2 / 1, h, d * Math.SQRT2 / 1); g.translate(0, h / 2, 0);
  return uvk(g, Math.max(w, d) * k * 1.4, Math.hypot(w, h) * k);
}

/* ── assembly helpers ────────────────────────────────────────────────────────────── */
export function part(g, geo, mat, x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0, shadow = true) {
  const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); if (rx || ry || rz) m.rotation.set(rx, ry, rz);
  m.castShadow = shadow; m.receiveShadow = true; g.add(m); return m;
}
const BOXG = new THREE.BoxGeometry(1, 1, 1), SPHG = new THREE.SphereGeometry(0.5, 12, 9), CYLG = new THREE.CylinderGeometry(0.5, 0.5, 1, 10);
export const box = (g, mat, w, h, d, x, y, z, ry = 0) => { const m = part(g, BOXG, mat, x, y, z, 0, ry, 0); m.scale.set(w, h, d); return m; };
export const ball = (g, mat, r, x, y, z, sy = 1) => { const m = part(g, SPHG, mat, x, y, z); m.scale.set(r * 2, r * 2 * sy, r * 2); return m; };
export const post = (g, mat, r, h, x, y, z) => { const m = part(g, CYLG, mat, x, y, z); m.scale.set(r * 2, h, r * 2); return m; };

/** a window: dark frame, glowing pane (lit at dusk by the shared material), sill */
export function windowAt(g, kit, x, y, z, w, h, ry = 0, shutters = false, cond = 100) {
  const wg = new THREE.Group(); wg.position.set(x, y, z); wg.rotation.y = ry; g.add(wg);
  const frame = kit.plain(0x2a1c10, cond, { rough: 0.9 });
  box(wg, frame, w + 0.03, h + 0.03, 0.02, 0, 0, 0);
  box(wg, kit.window, w, h, 0.02, 0, 0, 0.006).castShadow = false;
  box(wg, frame, 0.012, h, 0.024, 0, 0, 0.012); box(wg, frame, w, 0.012, 0.024, 0, 0, 0.012);
  box(wg, kit.tex('stone', 0xb0a696, cond), w + 0.06, 0.02, 0.05, 0, -h / 2 - 0.012, 0.015);
  if (shutters) for (const s of [-1, 1]) box(wg, kit.tex('planks', 0x4a6a3a, cond), w * 0.42, h + 0.02, 0.014, s * (w / 2 + w * 0.22), 0, 0.006);
  return wg;
}
export function doorAt(g, kit, x, y, z, w, h, ry = 0, cond = 100, arch = false) {
  const dg = new THREE.Group(); dg.position.set(x, y, z); dg.rotation.y = ry; g.add(dg);
  box(dg, kit.plain(0x2a1c10, cond), w + 0.04, h + 0.02, 0.025, 0, h / 2, 0);
  box(dg, kit.tex('planks', 0x6a4626, cond), w, h, 0.03, 0, h / 2, 0.006);
  if (arch) { const a = part(dg, new THREE.CylinderGeometry(w / 2, w / 2, 0.03, 12, 1, false, 0, Math.PI), kit.tex('planks', 0x6a4626, cond), 0, h, 0.006, Math.PI / 2, 0, Math.PI / 2); a.rotation.set(Math.PI / 2, 0, 0); }
  ball(dg, kit.plain(0xd8b040, 100, { metal: 0.9, rough: 0.3 }), 0.012, w * 0.3, h * 0.45, 0.026);
  return dg;
}
/** timber framing over a wall face: dark posts and rails that give plaster its character */
export function timberFrame(g, kit, w, h, d, y0, cond = 100) {
  const m = kit.tex('planks', 0x3a2818, cond, { ns: 0.5 }), t = 0.032;
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) box(g, m, t, h, t, sx * (w / 2), y0 + h / 2, sz * (d / 2));
  for (const sz of [-1, 1]) { box(g, m, w, t, t, 0, y0 + 0.004, sz * (d / 2)); box(g, m, w, t, t, 0, y0 + h - 0.004, sz * (d / 2)); }
  for (const sx of [-1, 1]) { box(g, m, t, t, d, sx * (w / 2), y0 + 0.004, 0); box(g, m, t, t, d, sx * (w / 2), y0 + h - 0.004, 0); }
  const dg = Math.hypot(w * 0.4, h);                                      // diagonal braces on the front and back
  for (const sz of [-1, 1]) for (const sx of [-1, 1]) { const b = box(g, m, t * 0.8, dg, t * 0.8, sx * w * 0.3, y0 + h / 2, sz * (d / 2 + 0.001)); b.rotation.z = sx * Math.atan2(w * 0.4, h); }
}

/** a torch: stick, ember cap, flame billboard, a flickering real light while the budget lasts */
export function torchAt(g, kit, fx, owner, x, y, z, { light = false } = {}) {
  post(g, kit.plain(0x3a2818, 100), 0.014, 0.28, x, y + 0.14, z);
  ball(g, kit.lantern, 0.022, x, y + 0.3, z);
  const flame = new THREE.Sprite(new THREE.SpriteMaterial({ map: kit.flameTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, color: 0xffa040 }));
  flame.scale.set(0.16, 0.22, 1); flame.position.set(x, y + 0.38, z); flame.userData.noAO = true; flame.userData.flame = true; g.add(flame);
  let pl = null;
  if (light && kit.lights.budget > 0) {
    kit.lights.budget--; pl = new THREE.PointLight(0xffa550, 0.5, 3.6, 2); pl.position.set(x, y + 0.45, z + 0.12); pl.userData.base = 0.5; g.add(pl); kit.lights.list.push(pl);
  }
  if (fx) fx.addEmitter(owner, 'fire', g, x, y + 0.38, z);
  return { flame, pl };
}

/** a waving banner: a subdivided plane whose vertices ripple */
export function bannerAt(g, kit, hex, w, h, x, y, z, ry = 0) {
  const geo = new THREE.PlaneGeometry(w, h, 8, 4); geo.translate(w / 2, -h / 2, 0);
  const m = part(g, geo, kit.cloth(hex), x, y, z, 0, ry, 0); m.castShadow = true;
  m.userData.rest = Float32Array.from(geo.attributes.position.array); m.userData.w = w; kit.banners.push(m); return m;
}
export function waveBanners(kit, t) {
  for (const b of kit.banners) {
    if (!b.parent) continue;
    const p = b.geometry.attributes.position, r = b.userData.rest, w = b.userData.w;
    for (let i = 0; i < p.count; i++) {
      const u = r[i * 3] / w;
      p.setZ(i, Math.sin(t * 3.2 + u * 7 + b.position.x * 3) * 0.05 * w * u);
      p.setY(i, r[i * 3 + 1] - Math.sin(t * 3.2 + u * 7) * 0.012 * u);
    }
    p.needsUpdate = true; b.geometry.computeVertexNormals();
  }
}
export const pick = (rnd, arr) => arr[(rnd() * arr.length) | 0];
export { srand, clamp };
