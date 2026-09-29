/* Phoenix 3D — the settlement's buildings. Each is assembled from the kit (textured PBR
   walls, gabled tile roofs, timber framing, windows that light at dusk), seeded by its
   placement id so the same building looks the same every load, and weathered by the
   condition the simulation reports. The town keep grows grander with each age. */
import * as THREE from 'three';
import { srand, clamp, lerp, glowTexture } from './util.js';
import { part, box, ball, post, texBox, texCyl, texCone, gableRoof, hipRoof, uvk, windowAt, doorAt, timberFrame, torchAt, bannerAt, pick } from './kit.js';
import { CIVIC, makeCivic } from './civic.js';

const C = (h) => new THREE.Color(h);

/* ── a home: plaster and timber, tiled or thatched roof, chimney that smokes ─────── */
function house(g, ctx, p, spec, rnd, cond) {
  const { kit, fx } = ctx;
  const two = rnd() < 0.4, w = 0.66 + rnd() * 0.16, d = w * (0.78 + rnd() * 0.12), h1 = 0.34 + rnd() * 0.06, h2 = two ? 0.28 : 0;
  const wall = C(spec.color || '#8b5a2b').multiplyScalar(1.6).lerp(C(0xe0c8a0), 0.45).getHex();
  box(g, kit.tex('stone', 0x9a9080, cond), w + 0.07, 0.3, d + 0.07, 0, -0.03, 0);           // plinth, sunk into the ground
  const y1 = 0.12;
  box(g, kit.tex('plaster', wall, cond), w, h1, d, 0, y1 + h1 / 2, 0);
  timberFrame(g, kit, w, h1, d, y1, cond);
  let top = y1 + h1, w2 = w, d2 = d;
  if (two) { w2 = w + 0.06; d2 = d + 0.06; box(g, kit.tex('plaster', wall, cond), w2, h2, d2, 0, top + h2 / 2, 0); timberFrame(g, kit, w2, h2, d2, top, cond); top += h2; }
  const rw = w2 + 0.14, rd = d2 + 0.18, rh = 0.26 + rnd() * 0.1, style = pick(rnd, ['tiles', 'tiles', 'thatch', 'slate']);
  const roofMat = style === 'thatch' ? kit.tex('thatch', 0xc7a866, cond) : kit.tex('tiles', style === 'slate' ? 0x5c6570 : pick(rnd, [0xb2532b, 0xa04a28, 0x8d4b2c]), cond);
  part(g, gableRoof(rw, rd, rh), roofMat, 0, top - 0.005, 0);
  box(g, kit.plain(0x3a2818, cond), rw + 0.02, 0.028, 0.03, 0, top + rh - 0.006, 0);        // ridge beam
  // chimney
  const cx = -w * 0.28, cz = -d * 0.12, ch = rh * 0.7 + 0.16;
  box(g, kit.tex('stone', 0x8a8074, cond), 0.1, ch, 0.1, cx, top + ch / 2 - 0.03, cz);
  box(g, kit.tex('stone', 0x6a6258, cond), 0.13, 0.03, 0.13, cx, top + ch - 0.03, cz);
  if (cond > 20 && fx) fx.addEmitter(p.id, 'smoke', g, cx, top + ch, cz);
  // front: door, step, lantern, windows
  const fz = d / 2 + 0.008;
  doorAt(g, kit, w * 0.08, y1, fz, 0.115, 0.21, 0, cond);
  box(g, kit.tex('stone', 0xa09686, cond), 0.22, 0.035, 0.09, w * 0.08, y1 - 0.012, fz + 0.045);
  ball(g, kit.lantern, 0.016, w * 0.08 + 0.1, y1 + 0.2, fz + 0.03);
  for (const s of [-1, 1]) windowAt(g, kit, w * 0.31 * s - w * 0.02, y1 + h1 * 0.6, fz + 0.002, 0.09, 0.1, 0, true, cond);
  windowAt(g, kit, w / 2 + 0.008, y1 + h1 * 0.6, 0, 0.08, 0.09, Math.PI / 2, false, cond);
  windowAt(g, kit, -w / 2 - 0.008, y1 + h1 * 0.6, 0, 0.08, 0.09, -Math.PI / 2, false, cond);
  if (two) for (const s of [-1, 1]) windowAt(g, kit, w * 0.27 * s, top - h2 * 0.5, d2 / 2 + 0.01, 0.085, 0.1, 0, true, cond);
  // props: a barrel and firewood by the wall
  if (rnd() < 0.6) {
    const barrel = part(g, new THREE.CylinderGeometry(0.045, 0.04, 0.09, 10), kit.tex('planks', 0x7a5530, cond), w / 2 - 0.02, y1 + 0.045, d / 2 + 0.06);
    void barrel; box(g, kit.plain(0x2a2a2a, 100, { metal: 0.6 }), 0.092, 0.008, 0.092, w / 2 - 0.02, y1 + 0.05, d / 2 + 0.06);
  }
  if (rnd() < 0.5) for (let i = 0; i < 4; i++) part(g, new THREE.CylinderGeometry(0.018, 0.018, 0.14, 6), kit.tex('planks', 0x8a6a40, cond),
    -w / 2 - 0.05, y1 + 0.018 + (i > 1 ? 0.034 : 0), -d * 0.2 + (i % 2) * 0.04 + (i > 1 ? 0.02 : 0), Math.PI / 2, 0, 0);
  g.userData.hearth = { x: cx, y: top, z: cz };
}

/* ── the windmill: stone tower, timber cap, four lattice sails that turn ─────────── */
function mill(g, ctx, p, spec, rnd, cond) {
  const { kit, mills } = ctx, tw = 0.34, th = 1.02 + rnd() * 0.2;
  const stone = kit.tex('stone', C(spec.color || '#c9b98f').multiplyScalar(0.95).getHex(), cond);
  part(g, texCyl(tw * 0.7, tw, th, 12), stone, 0, th / 2 - 0.02, 0);
  part(g, texCyl(tw * 1.08, tw * 1.12, 0.09, 12), kit.tex('stone', 0x8a8074, cond), 0, 0.02, 0);          // footing ring
  part(g, texCone(tw * 0.86, 0.46, 12), kit.tex('tiles', 0x6c3a26, cond), 0, th + 0.2, 0);                // cap
  part(g, texCyl(tw * 0.74, tw * 0.74, 0.06, 12), kit.tex('planks', 0x5a3c22, cond), 0, th - 0.015, 0);      // cap ring
  doorAt(g, kit, 0, 0, tw * 0.99, 0.15, 0.26, 0, cond, true);
  for (const f of [0.5, 0.75]) windowAt(g, kit, 0, th * f, tw * (1 - 0.3 * f) + 0.015, 0.07, 0.11, 0, false, cond);
  for (let i = 0; i < 3; i++) part(g, new THREE.SphereGeometry(0.06, 8, 6), kit.plain(0xe6dcc0, cond, { rough: 1 }), 0.24 + i * 0.09, 0.05, tw * 0.9 + (i % 2) * 0.05).scale.set(1, 0.8, 0.8);
  const hub = new THREE.Group(); hub.position.set(0, th * 0.87, tw * 0.74); g.add(hub);
  part(hub, new THREE.CylinderGeometry(0.04, 0.05, 0.16, 8), kit.plain(0x3a2818, cond), 0, 0, -0.04, Math.PI / 2, 0, 0);
  const beam = kit.tex('planks', 0x7a5a36, cond), sail = kit.cloth('#e8dcc0');
  for (let i = 0; i < 4; i++) {
    const arm = new THREE.Group(); arm.rotation.z = i * Math.PI / 2; hub.add(arm);
    box(arm, beam, 0.032, 0.98, 0.022, 0, 0.49, 0.03);
    for (const y of [0.22, 0.44, 0.66, 0.88]) box(arm, beam, 0.15, 0.014, 0.014, 0.06, y, 0.03);
    box(arm, beam, 0.012, 0.66, 0.012, 0.13, 0.55, 0.03);
    const cloth = part(arm, new THREE.PlaneGeometry(0.1, 0.62), sail, 0.065, 0.55, 0.036); cloth.castShadow = true;
  }
  mills.push({ hub, speed: (0.5 + rnd() * 1.2) * (rnd() < 0.5 ? -1 : 1) });
}

/* ── camps: the woodcutters' and the miners' yards ───────────────────────────────── */
function camp(g, ctx, p, spec, rnd, cond) {
  const { kit, fx } = ctx, wood = (spec.accent || '') === '#b5793a', fp = 0.62;
  if (wood) {
    for (const [px, pz] of [[-fp / 2, -fp / 2.4], [fp / 2, -fp / 2.4], [-fp / 2, fp / 2.4], [fp / 2, fp / 2.4]]) post(g, kit.tex('planks', 0x6a4a28, cond), 0.03, 0.42, px, 0.21, pz);
    part(g, gableRoof(fp + 0.16, fp * 0.95 + 0.14, 0.24), kit.tex('thatch', 0xb99a58, cond), 0, 0.4, 0);
    box(g, kit.tex('planks', 0x6a4a28, cond), fp, 0.03, 0.03, 0, 0.4, fp / 2.4); box(g, kit.tex('planks', 0x6a4a28, cond), fp, 0.03, 0.03, 0, 0.4, -fp / 2.4);
    // log pile with light end-grain
    const bark = kit.tex('planks', 0x5b3e22, cond, { ns: 1.4 }), endg = kit.plain(0xc9a06a, cond, { rough: 0.9 });
    for (let row = 0; row < 3; row++) for (let i = 0; i < 4 - row; i++) {
      const lg = new THREE.CylinderGeometry(0.038, 0.038, 0.44, 8); uvk(lg, 1, 1);
      part(g, lg, [bark, endg, endg], -0.12 + (i + row * 0.5) * 0.09 - 0.14, 0.04 + row * 0.07, fp / 2 + 0.16, 0, 0, Math.PI / 2 + 0.02);
    }
    // chopping block, axe, sawhorse
    part(g, new THREE.CylinderGeometry(0.075, 0.085, 0.1, 10), [kit.tex('planks', 0x6a4a28, cond), endg, endg], 0.3, 0.05, -0.1);
    box(g, kit.tex('planks', 0x8a6a40, cond), 0.018, 0.16, 0.018, 0.3, 0.16, -0.1).rotation.z = 0.5;
    box(g, kit.plain(0x8d9096, cond, { metal: 0.85, rough: 0.35 }), 0.07, 0.05, 0.012, 0.36, 0.22, -0.1).rotation.z = 0.5;
    for (const s of [-1, 1]) { const lg = box(g, kit.tex('planks', 0x6a4a28, cond), 0.022, 0.2, 0.022, -0.3 + s * 0.09, 0.1, -0.22); lg.rotation.x = 0.35 * s; }
    box(g, kit.tex('planks', 0x6a4a28, cond), 0.26, 0.026, 0.026, -0.3, 0.19, -0.22);
    torchAt(g, kit, fx, p.id, fp / 2 + 0.06, 0.02, -fp / 2.4 - 0.05);
  } else {
    // mine mouth in a rock mound with a timber frame, ore cart on rails, lantern
    const mound = part(g, new THREE.SphereGeometry(0.5, 14, 10), kit.tex('rock', 0x9a8f80, cond), 0, 0.02, -0.16); mound.scale.set(0.95, 0.62, 0.7);
    const tm = kit.tex('planks', 0x5a3c22, cond);
    box(g, kit.plain(0x050403, 100), 0.26, 0.3, 0.06, 0, 0.17, 0.12);
    post(g, tm, 0.022, 0.34, -0.15, 0.17, 0.14); post(g, tm, 0.022, 0.34, 0.15, 0.17, 0.14); box(g, tm, 0.36, 0.04, 0.05, 0, 0.35, 0.14);
    ball(g, kit.lantern, 0.022, 0.19, 0.28, 0.17); torchAt(g, kit, fx, p.id, -0.24, 0.02, 0.18);
    for (const s of [-1, 1]) box(g, kit.plain(0x4a4a4a, cond, { metal: 0.6 }), 0.018, 0.012, 0.6, s * 0.075, 0.008, 0.42);
    for (let i = 0; i < 6; i++) box(g, tm, 0.22, 0.012, 0.03, 0, 0.004, 0.16 + i * 0.1);
    const cart = new THREE.Group(); cart.position.set(0, 0.06, 0.4); g.add(cart);
    part(cart, new THREE.CylinderGeometry(0.11, 0.08, 0.1, 4), kit.tex('planks', 0x6a4a28, cond), 0, 0.06, 0, 0, Math.PI / 4, 0).scale.set(1.15, 1, 0.85);
    for (const [wx, wz] of [[-0.09, -0.06], [0.09, -0.06], [-0.09, 0.06], [0.09, 0.06]]) part(cart, new THREE.CylinderGeometry(0.03, 0.03, 0.02, 10), kit.plain(0x2a2a2a, cond, { metal: 0.7 }), wx, 0, wz, 0, 0, Math.PI / 2);
    const ore = kit.plain(C(spec.accent || '#e0b23a').getHex(), cond, { metal: 0.85, rough: 0.3, em: spec.accent || '#e0b23a', ei: 0.25 });
    for (let i = 0; i < 7; i++) { const n = ball(cart, ore, 0.034 + rnd() * 0.02, (rnd() - 0.5) * 0.14, 0.13 + rnd() * 0.03, (rnd() - 0.5) * 0.08); n.rotation.set(rnd() * 3, rnd() * 3, 0); }
    for (let i = 0; i < 5; i++) ball(g, ore, 0.03 + rnd() * 0.02, -0.3 + rnd() * 0.16, 0.03, 0.3 + rnd() * 0.1);
    for (const s of [-1, 1]) { box(g, tm, 0.014, 0.2, 0.014, 0.3 + s * 0.02, 0.1, 0.14).rotation.z = 0.35 * s; }
    if (fx) fx.addEmitter(p.id, 'sparkle', g, 0, 0.16, 0.4, { color: 0xffe08a });
  }
}

/* ── tech: the wheelbarrow, and a generic rune-stone for anything else ───────────── */
function tech(g, ctx, p, spec, rnd, cond) {
  const { kit, fx } = ctx, m = kit.tex('planks', 0x8a6238, cond);
  if (p.name === 'wheelbarrow') {
    part(g, new THREE.CylinderGeometry(0.1, 0.075, 0.09, 4), m, 0, 0.12, -0.02, 0, Math.PI / 4, 0).scale.set(1.5, 1, 1);
    part(g, new THREE.CylinderGeometry(0.06, 0.06, 0.03, 12), kit.plain(0x2a2a2a, cond, { metal: 0.7, rough: 0.4 }), 0, 0.06, 0.15, 0, 0, Math.PI / 2);
    for (const s of [-1, 1]) { box(g, m, 0.014, 0.014, 0.3, s * 0.06, 0.13, -0.24).rotation.x = -0.12; box(g, m, 0.014, 0.1, 0.014, s * 0.07, 0.05, -0.12); }
    for (let i = 0; i < 6; i++) ball(g, kit.plain(0x4a3520, cond, { rough: 1 }), 0.03 + rnd() * 0.015, (rnd() - 0.5) * 0.12, 0.18 + rnd() * 0.02, (rnd() - 0.5) * 0.08);
    box(g, kit.tex('cobble', 0x8a8070, cond), 0.5, 0.02, 0.55, 0, 0.005, -0.02);
    return;
  }
  box(g, kit.tex('stone', 0xa09686, cond), 0.42, 0.12, 0.42, 0, 0.04, 0);
  box(g, kit.tex('stone', 0xb0a696, cond), 0.28, 0.32, 0.28, 0, 0.28, 0);
  const badge = part(g, new THREE.OctahedronGeometry(0.11, 0), kit.plain(C(spec.color || '#c9b98f').getHex(), 100, { metal: 0.6, rough: 0.25, em: spec.color || '#c9b98f', ei: 0.7 }), 0, 0.68, 0);
  badge.scale.set(0.9, 1.3, 0.9); g.userData.float = badge; if (fx) fx.addEmitter(p.id, 'sparkle', g, 0, 0.7, 0, { color: C(spec.color || '#c9b98f').getHex() });
}

/* ── anything the world invents that we have no model for: a floating gem shrine ─── */
function gem(g, ctx, p, spec, rnd, cond) {
  const { kit, fx } = ctx, hex = C(spec.color || '#8ab4ff').getHex();
  for (let i = 0; i < 3; i++) part(g, new THREE.CylinderGeometry(0.2 - i * 0.05, 0.24 - i * 0.05, 0.06, 8), kit.tex('stone', 0xa09686, cond), 0, 0.03 + i * 0.06, 0);
  const mat = new THREE.MeshPhysicalMaterial({ color: hex, metalness: 0.2, roughness: 0.08, clearcoat: 1, emissive: hex, emissiveIntensity: 0.55, envMapIntensity: 1.8, flatShading: true });
  const stone = part(g, new THREE.OctahedronGeometry(0.16, 0), mat, 0, 0.5, 0); stone.scale.set(0.9, 1.35, 0.9); g.userData.float = stone;
  for (let i = 0; i < 3; i++) { const s = part(g, new THREE.OctahedronGeometry(0.04, 0), mat, Math.cos(i * 2.1) * 0.24, 0.42 + i * 0.05, Math.sin(i * 2.1) * 0.24); s.userData.orbit = i; }
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: ctx.kit.flameTex, color: hex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.7 }));
  halo.scale.setScalar(0.9); halo.position.y = 0.5; halo.userData.noAO = true; g.add(halo);
  if (fx) fx.addEmitter(p.id, 'sparkle', g, 0, 0.5, 0, { color: hex });
}

/* ── entry point ─────────────────────────────────────────────────────────────────── */
export function makeBuilding(p, reg, ctx) {
  const spec = reg[p.name] || { shape: 'diamond', color: '#8ab4ff' }, rnd = srand((p.id * 2654435761) >>> 0), cond = p.condition == null ? 100 : p.condition;
  const g = new THREE.Group(), { field } = ctx, X = field.wx(p.x), Z = field.wz(p.y);
  g.position.set(X, field.heightAt(X, Z), Z);
  g.rotation.y = (rnd() * 4 | 0) * Math.PI / 2 + (rnd() - 0.5) * 0.24;
  if (cond < 50) g.rotation.z = 0.05 + rnd() * 0.04;                     // leaning: it needs repair
  const s = spec.shape;
  if (s === 'house') house(g, ctx, p, spec, rnd, cond);
  else if (s === 'mill') mill(g, ctx, p, spec, rnd, cond);
  else if (s === 'camp') camp(g, ctx, p, spec, rnd, cond);
  else if (s === 'tech') tech(g, ctx, p, spec, rnd, cond);
  else if (CIVIC.has(s)) makeCivic(g, ctx, p, spec, rnd, cond);
  else gem(g, ctx, p, spec, rnd, cond);
  g.userData.info = { p, spec };
  g.traverse((o) => { o.userData.info = g.userData.info; });
  return g;
}

/* ── the keep: grows with the age ────────────────────────────────────────────────── */
export function makeKeep(ctx, map, ageIndex) {
  const { kit, fx, field } = ctx, g = new THREE.Group(), lvl = Math.min(4, Math.floor(ageIndex / 2)), cond = 100;
  const T = field.town; g.position.set(T.x, field.heightAt(T.x, T.z), T.z);
  const stone = kit.tex('stone', 0xb8ae9c, cond), dark = kit.tex('stone', 0x8f8676, cond), plaster = kit.tex('plaster', 0xe4d6b4, cond);
  const tiles = kit.tex('tiles', 0x8a3a26, cond), slate = kit.tex('tiles', 0x4f5862, cond), oak = kit.tex('planks', 0x5a3c22, cond);
  box(g, dark, 2.2, 0.34, 1.9, 0, -0.02, 0);                                                    // plinth
  box(g, kit.tex('stone', 0xa89e8c, cond), 2.0, 0.08, 1.7, 0, 0.18, 0);
  for (let i = 0; i < 3; i++) box(g, stone, 0.7, 0.05, 0.14, 0, 0.16 - i * 0.04, 1.02 + i * 0.1);   // steps
  // hall: stone ground storey, timbered upper storey
  box(g, stone, 1.5, 0.66, 1.15, 0, 0.55, 0);
  box(g, plaster, 1.42, 0.5, 1.1, 0, 1.13, 0); timberFrame(g, kit, 1.42, 0.5, 1.1, 0.88, cond);
  part(g, gableRoof(1.7, 1.42, 0.72), tiles, 0, 1.36, 0);
  box(g, oak, 1.74, 0.04, 0.04, 0, 2.06, 0);
  for (const s of [-1, 1]) {                                                                    // dormers
    box(g, plaster, 0.26, 0.24, 0.2, s * 0.42, 1.55, 0.5); part(g, gableRoof(0.34, 0.3, 0.16), tiles, s * 0.42, 1.65, 0.5);
    windowAt(g, kit, s * 0.42, 1.55, 0.605, 0.1, 0.13, 0, true, cond);
  }
  // corner towers with conical roofs, arrow slits and pennants
  const tp = [[-0.9, -0.66], [0.9, -0.66], [-0.9, 0.66], [0.9, 0.66]];
  for (const [i, [tx, tz]] of tp.entries()) {
    part(g, texCyl(0.24, 0.27, 1.32, 12), stone, tx, 0.72, tz);
    part(g, texCyl(0.29, 0.27, 0.1, 12), dark, tx, 1.4, tz);
    part(g, texCone(0.31, 0.62, 12), slate, tx, 1.76, tz);
    windowAt(g, kit, tx, 0.95, tz + (tz > 0 ? 0.255 : -0.255), 0.05, 0.14, tz > 0 ? 0 : Math.PI, false, cond);
    post(g, oak, 0.008, 0.34, tx, 2.2, tz); bannerAt(g, kit, i % 2 ? '#e0b23a' : '#7a2f22', 0.2, 0.11, tx, 2.36, tz, 0);
  }
  // gate with portcullis, flanking torches
  const gz = 0.582;
  box(g, kit.plain(0x080605, 100), 0.34, 0.44, 0.04, 0, 0.5, gz);
  box(g, dark, 0.46, 0.06, 0.08, 0, 0.75, gz + 0.02); post(g, stone, 0.03, 0.5, -0.2, 0.5, gz + 0.02); post(g, stone, 0.03, 0.5, 0.2, 0.5, gz + 0.02);
  for (let i = -3; i <= 3; i++) box(g, kit.plain(0x2c2c30, 100, { metal: 0.8, rough: 0.4 }), 0.012, 0.42, 0.012, i * 0.045, 0.52, gz + 0.03);
  for (let j = 0; j < 4; j++) box(g, kit.plain(0x2c2c30, 100, { metal: 0.8 }), 0.32, 0.012, 0.012, 0, 0.36 + j * 0.1, gz + 0.03);
  torchAt(g, kit, fx, 'keep', -0.34, 0.42, gz + 0.05, { light: true }); torchAt(g, kit, fx, 'keep', 0.34, 0.42, gz + 0.05, { light: true });
  for (const x of [-0.5, 0.5]) windowAt(g, kit, x, 0.62, 0.585, 0.1, 0.16, 0, false, cond);
  windowAt(g, kit, 0, 1.15, 0.565, 0.16, 0.2, 0, true, cond);
  for (const s of [-1, 1]) windowAt(g, kit, s * 0.76, 1.13, 0.2, 0.12, 0.2, s * Math.PI / 2, false, cond);
  // chimneys and smoke
  box(g, stone, 0.14, 0.5, 0.14, -0.55, 2.0, -0.2); box(g, stone, 0.14, 0.5, 0.14, 0.5, 2.0, -0.2);
  fx && (fx.addEmitter('keep', 'smoke', g, -0.55, 2.28, -0.2), fx.addEmitter('keep', 'smoke', g, 0.5, 2.28, -0.2));
  // the great banner on its mast
  post(g, oak, 0.02, 1.5, 0, 2.7, -0.05); ball(g, kit.plain(0xd8b040, 100, { metal: 0.9, rough: 0.3 }), 0.035, 0, 3.46, -0.05);
  const flag = bannerAt(g, kit, '#e0b23a', 0.5, 0.3, 0.02, 3.32, -0.05, 0);
  // ── growth by age ──
  if (lvl >= 1) {                                                                               // gatehouse wing
    box(g, stone, 0.7, 0.8, 0.5, 0, 0.55, 1.05 + 0.1); part(g, gableRoof(0.86, 0.66, 0.36), tiles, 0, 0.95, 1.15, 0, Math.PI / 2, 0);
    doorAt(g, kit, 0, 0.14, 1.42, 0.3, 0.42, 0, cond, true); torchAt(g, kit, fx, 'keep', -0.3, 0.14, 1.5); torchAt(g, kit, fx, 'keep', 0.3, 0.14, 1.5);
  }
  if (lvl >= 2) {                                                                               // curtain wall with merlons
    const wallM = stone;
    for (const [w, d, x, z] of [[2.9, 0.16, 0, -1.28], [0.16, 2.2, -1.5, 0], [0.16, 2.2, 1.5, 0]]) {
      box(g, wallM, w, 0.5, d, x, 0.25, z);
      const n = Math.floor((w > d ? w : d) / 0.22);
      for (let i = 0; i < n; i++) { const o = (i - (n - 1) / 2) * 0.22; box(g, wallM, w > d ? 0.12 : d + 0.02, 0.12, w > d ? d + 0.02 : 0.12, x + (w > d ? o : 0), 0.56, z + (w > d ? 0 : o)); }
    }
    for (const [x, z] of [[-1.5, -1.28], [1.5, -1.28]]) { part(g, texCyl(0.2, 0.22, 0.85, 10), stone, x, 0.42, z); part(g, texCone(0.26, 0.4, 10), slate, x, 1.05, z); }
  }
  if (lvl >= 3) {                                                                               // central donjon
    box(g, stone, 0.5, 1.0, 0.5, 0, 2.55, -0.32); part(g, hipRoof(0.6, 0.6, 0.5), slate, 0, 3.05, -0.32);
    for (const y of [2.3, 2.7]) windowAt(g, kit, 0, y, -0.065, 0.08, 0.14, 0, false, cond);
  }
  if (lvl >= 4) {                                                                               // gilded spire and beacon ring
    const gold = kit.plain(0xe0b23a, 100, { metal: 0.95, rough: 0.22, em: 0xe0b23a, ei: 0.35 });
    part(g, new THREE.ConeGeometry(0.06, 0.6, 8), gold, 0, 3.6, -0.32); ball(g, gold, 0.07, 0, 3.9, -0.32);
    const ring = part(g, new THREE.TorusGeometry(0.32, 0.012, 6, 32), gold, 0, 2.6, -0.32, Math.PI / 2, 0, 0); ring.userData.spin = true;
  }
  // the herald's beacon: a pillar of light and a real light while a decision waits on the human
  const bcv = document.createElement('canvas'); bcv.width = 64; bcv.height = 256;
  { const c = bcv.getContext('2d'), lg = c.createLinearGradient(0, 256, 0, 0); lg.addColorStop(0, 'rgba(255,214,120,0.7)'); lg.addColorStop(1, 'rgba(255,214,120,0)'); c.fillStyle = lg; c.fillRect(0, 0, 64, 256); }
  const beacon = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.2, 5.2, 14, 1, true), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(bcv), color: 0xe0b23a,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide, fog: false }));
  beacon.position.set(0, 5.1, 0); beacon.visible = false; beacon.userData.noAO = true; g.add(beacon);
  const beaconLight = new THREE.PointLight(0xe0b23a, 0, 12, 2); beaconLight.position.set(0, 2.6, 0); g.add(beaconLight);
  g.traverse((o) => { if (o.isMesh) { o.userData.keep = true; } });
  g.userData = { flag, beacon, beaconLight, level: lvl };
  return g;
}
