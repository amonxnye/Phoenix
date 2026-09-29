/* Phoenix 3D — Article XII made visible: the civic works the world designs for itself.
   The simulation chooses a shape, a colour, a scale and a district; this draws it. Every one
   of the twelve shapes is a small piece of architecture, not a token. */
import * as THREE from 'three';
import { srand } from './util.js';
import { part, box, ball, post, texBox, texCyl, texDisc, texCone, gableRoof, hipRoof, uvk, windowAt, doorAt, torchAt, bannerAt } from './kit.js';

export const CIVIC = new Set(['plaza', 'garden', 'fountain', 'tower', 'temple', 'monument', 'lamp', 'grove', 'aqueduct', 'library', 'wall', 'road']);
const C = (h) => new THREE.Color(h);

const shapes = {
  plaza(s, ctx, p, col, cond, rnd) {
    const { kit, fx } = ctx;
    part(s, texDisc(0.86, 0.9, 0.06, 28), kit.tex('cobble', 0xb0a48c, cond), 0, 0.02, 0);
    part(s, texDisc(0.56, 0.58, 0.075, 28), kit.tex('cobble', col.getHex(), cond, { ns: 0.6 }), 0, 0.03, 0);
    // a stone well at the heart
    part(s, texCyl(0.17, 0.19, 0.16, 12), kit.tex('stone', 0xa89e8c, cond), 0, 0.14, 0);
    part(s, new THREE.CylinderGeometry(0.13, 0.13, 0.01, 12), kit.plain(0x2c6f96, 100, { rough: 0.05, metal: 0.1 }), 0, 0.215, 0);
    for (const sx of [-1, 1]) post(s, kit.tex('planks', 0x6a4a28, cond), 0.012, 0.28, sx * 0.15, 0.32, 0);
    part(s, gableRoof(0.44, 0.24, 0.13), kit.tex('tiles', 0x8a3a26, cond), 0, 0.46, 0, 0, 0, 0);
    for (let i = 0; i < 4; i++) {                                                             // lamp posts and benches
      const a = i * Math.PI / 2 + Math.PI / 4, x = Math.cos(a) * 0.72, z = Math.sin(a) * 0.72;
      post(s, kit.plain(0x2a2a30, cond, { metal: 0.7 }), 0.01, 0.34, x, 0.2, z); ball(s, kit.lantern, 0.028, x, 0.4, z);
      const bx = Math.cos(a + Math.PI / 4) * 0.7, bz = Math.sin(a + Math.PI / 4) * 0.7;
      box(s, kit.tex('planks', 0x7a5530, cond), 0.24, 0.03, 0.07, bx, 0.1, bz, -(a + Math.PI / 4) + Math.PI / 2);
      for (const o of [-0.09, 0.09]) box(s, kit.tex('stone', 0x8a8074, cond), 0.03, 0.08, 0.06, bx + Math.cos(a + Math.PI / 4 + Math.PI / 2) * o, 0.05, bz + Math.sin(a + Math.PI / 4 + Math.PI / 2) * o);
    }
  },
  garden(s, ctx, p, col, cond, rnd) {
    const { kit } = ctx;
    box(s, kit.plain(0x4f8a3c, cond, { rough: 1 }), 1.3, 0.03, 1.3, 0, 0.012, 0);
    box(s, kit.tex('cobble', 0xb8aa8c, cond), 0.14, 0.05, 1.3, 0, 0.03, 0); box(s, kit.tex('cobble', 0xb8aa8c, cond), 1.3, 0.05, 0.14, 0, 0.032, 0);
    const hedge = kit.tex('leaf', 0x2f6a2a, cond, { ns: 1.2 });
    for (const sz of [-1, 1]) { box(s, hedge, 1.36, 0.14, 0.09, 0, 0.09, sz * 0.66); box(s, hedge, 0.09, 0.14, 1.36, sz * 0.66, 0.09, 0); }
    for (const [bx, bz] of [[-0.33, -0.33], [0.33, -0.33], [-0.33, 0.33], [0.33, 0.33]]) {
      box(s, kit.plain(0x4a3320, cond, { rough: 1 }), 0.4, 0.06, 0.4, bx, 0.06, bz);
      for (let i = 0; i < 9; i++) { const f = ball(s, kit.plain(col.clone().offsetHSL((rnd() - 0.5) * 0.12, 0, (rnd() - 0.5) * 0.2).getHex(), cond, { rough: 0.6 }),
        0.028 + rnd() * 0.014, bx + (rnd() - 0.5) * 0.34, 0.13 + rnd() * 0.05, bz + (rnd() - 0.5) * 0.34); f.castShadow = false; }
    }
    post(s, kit.tex('stone', 0xa89e8c, cond), 0.04, 0.16, 0, 0.1, 0); part(s, new THREE.CylinderGeometry(0.1, 0.1, 0.02, 14), kit.plain(0xc2a860, cond, { metal: 0.8, rough: 0.35 }), 0, 0.2, 0);
    box(s, kit.plain(0xc2a860, cond, { metal: 0.8 }), 0.16, 0.005, 0.012, 0, 0.215, 0);
  },
  fountain(s, ctx, p, col, cond, rnd) {
    const { kit, fx } = ctx, st = kit.tex('stone', 0xb8ae9c, cond);
    part(s, texDisc(0.52, 0.55, 0.16, 24), st, 0, 0.08, 0); part(s, new THREE.CylinderGeometry(0.47, 0.47, 0.01, 24), kit.plain(0x2d86b8, 100, { rough: 0.03, metal: 0.05, tr: 0.88 }), 0, 0.15, 0);
    part(s, texCyl(0.06, 0.1, 0.48, 10), st, 0, 0.36, 0);
    part(s, texCyl(0.24, 0.1, 0.09, 18), st, 0, 0.6, 0); part(s, new THREE.CylinderGeometry(0.2, 0.2, 0.008, 18), kit.plain(0x2d86b8, 100, { rough: 0.03, tr: 0.9 }), 0, 0.645, 0);
    part(s, texCyl(0.03, 0.05, 0.2, 8), st, 0, 0.75, 0);
    const jet = ball(s, kit.plain(col.getHex(), 100, { em: col.getHex(), ei: 1.3, rough: 0.2 }), 0.045, 0, 0.9, 0); s.userData.float = jet;
    if (fx) { fx.addEmitter(p.id, 'spray', s, 0, 0.88, 0); fx.addEmitter(p.id, 'spray', s, 0, 0.66, 0); }
    for (let i = 0; i < 8; i++) { const a = i / 8 * 6.28; box(s, st, 0.09, 0.05, 0.06, Math.cos(a) * 0.54, 0.17, Math.sin(a) * 0.54, -a + Math.PI / 2); }
  },
  tower(s, ctx, p, col, cond, rnd) {
    const { kit, fx } = ctx, st = kit.tex('stone', 0xb0a696, cond);
    part(s, texCyl(0.27, 0.32, 1.7, 14), st, 0, 0.85, 0); part(s, texCyl(0.36, 0.32, 0.14, 14), kit.tex('stone', 0x8f8676, cond), 0, 1.74, 0);
    for (let i = 0; i < 9; i++) { const a = i / 9 * 6.28; box(s, st, 0.1, 0.12, 0.08, Math.cos(a) * 0.34, 1.87, Math.sin(a) * 0.34, -a + Math.PI / 2); }
    part(s, texCone(0.25, 0.42, 12), kit.tex('tiles', col.getHex(), cond), 0, 1.86, 0).scale.set(0.9, 1, 0.9);
    for (const y of [0.55, 1.0, 1.4]) windowAt(s, kit, 0, y, 0.3 - y * 0.03, 0.05, 0.14, 0, false, cond);
    doorAt(s, kit, 0, 0, 0.318, 0.14, 0.25, 0, cond, true);
    bannerAt(s, kit, col.getHex(), 0.2, 0.34, 0.28, 1.68, 0.05, Math.PI / 2 * 0);
    torchAt(s, kit, fx, p.id, 0, 1.6, 0.38, { light: true });
  },
  temple(s, ctx, p, col, cond, rnd) {
    const { kit, fx } = ctx, marble = kit.tex('plaster', C(0xeee6d4).lerp(col, 0.25).getHex(), cond, { rough: 0.55 });
    for (let i = 0; i < 3; i++) box(s, kit.tex('stone', 0xc8bfa8, cond), 1.42 - i * 0.06, 0.06, 1.06 - i * 0.06, 0, 0.03 + i * 0.06, 0);
    for (let i = 0; i < 4; i++) for (const z of [-0.36, 0.36]) {
      const x = -0.5 + i * 0.333;
      part(s, new THREE.CylinderGeometry(0.052, 0.062, 0.62, 14), marble, x, 0.52, z); box(s, marble, 0.14, 0.04, 0.14, x, 0.85, z); box(s, marble, 0.13, 0.03, 0.13, x, 0.21, z);
    }
    box(s, marble, 1.36, 0.1, 0.96, 0, 0.92, 0); box(s, kit.plain(col.getHex(), 100, { rough: 0.5 }), 1.38, 0.03, 0.98, 0, 0.98, 0);
    part(s, gableRoof(1.48, 1.04, 0.36), kit.tex('tiles', col.clone().multiplyScalar(0.9).getHex(), cond), 0, 0.99, 0);
    box(s, kit.tex('stone', 0xd8d0bc, cond), 0.5, 0.42, 0.34, 0, 0.42, 0);                  // cella
    const altar = ball(s, kit.plain(col.getHex(), 100, { em: col.getHex(), ei: 1.6 }), 0.06, 0, 0.5, 0.2); s.userData.float = altar;
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: kit.flameTex, color: col.getHex(), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.65 }));
    halo.scale.setScalar(0.7); halo.position.set(0, 0.5, 0.2); halo.userData.noAO = true; s.add(halo);
    if (fx) fx.addEmitter(p.id, 'sparkle', s, 0, 0.55, 0.2, { color: col.getHex() });
  },
  monument(s, ctx, p, col, cond, rnd) {
    const { kit, fx } = ctx, st = kit.tex('stone', 0xb8ae9c, cond);
    for (let i = 0; i < 3; i++) box(s, st, 0.7 - i * 0.14, 0.1, 0.7 - i * 0.14, 0, 0.05 + i * 0.1, 0);
    part(s, new THREE.CylinderGeometry(0.05, 0.13, 1.4, 4), kit.tex('stone', C(0xc8bfa8).lerp(col, 0.18).getHex(), cond, { rough: 0.6 }), 0, 1.0, 0, 0, Math.PI / 4, 0);
    part(s, new THREE.ConeGeometry(0.06, 0.16, 4), kit.plain(0xe0b23a, 100, { metal: 0.95, rough: 0.2 }), 0, 1.78, 0, 0, Math.PI / 4, 0);
    const tip = part(s, new THREE.OctahedronGeometry(0.11, 0), new THREE.MeshPhysicalMaterial({ color: col.getHex(), emissive: col.getHex(), emissiveIntensity: 0.9, roughness: 0.1, clearcoat: 1, flatShading: true }), 0, 2.05, 0);
    tip.scale.set(0.8, 1.3, 0.8); s.userData.float = tip;
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: kit.flameTex, color: col.getHex(), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.6 }));
    halo.scale.setScalar(0.9); halo.position.y = 2.05; halo.userData.noAO = true; s.add(halo);
    if (fx) fx.addEmitter(p.id, 'sparkle', s, 0, 2.0, 0, { color: col.getHex() });
  },
  lamp(s, ctx, p, col, cond, rnd) {
    const { kit } = ctx, iron = kit.plain(0x26262c, cond, { metal: 0.75, rough: 0.45 });
    for (const [i, x] of [-0.42, 0, 0.42].entries()) {
      post(s, iron, 0.014, 0.9, x, 0.45, 0); post(s, iron, 0.03, 0.06, x, 0.03, 0);
      const arm = box(s, iron, 0.14, 0.012, 0.012, x + 0.06, 0.88, 0); void arm;
      box(s, kit.plain(col.getHex(), 100, { em: col.getHex(), ei: 1.8, rough: 0.2 }), 0.07, 0.1, 0.07, x + 0.12, 0.82, 0);
      part(s, new THREE.ConeGeometry(0.06, 0.05, 4), iron, x + 0.12, 0.9, 0, 0, Math.PI / 4, 0);
      const g = new THREE.Sprite(new THREE.SpriteMaterial({ map: kit.flameTex, color: col.getHex(), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.55 }));
      g.scale.setScalar(0.42); g.position.set(x + 0.12, 0.82, 0); g.userData.noAO = true; s.add(g);
      const pool = new THREE.Mesh(new THREE.CircleGeometry(0.34, 20), new THREE.MeshBasicMaterial({ map: kit.flameTex, color: col.getHex(), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.35 }));
      pool.rotation.x = -Math.PI / 2; pool.position.set(x + 0.12, 0.012, 0); pool.userData.noAO = true; pool.userData.lamp = true; s.add(pool);
    }
    if (kit.lights.budget > 0) { kit.lights.budget--; const pl = new THREE.PointLight(col.getHex(), 1.1, 3.4, 2); pl.position.set(0.12, 0.85, 0.1); pl.userData.base = 1.1; s.add(pl); kit.lights.list.push(pl); }
  },
  grove(s, ctx, p, col, cond, rnd) {
    const { kit } = ctx;
    for (let i = 0; i < 7; i++) {
      const k = ['oak', 'birch', 'pine'][i % 3], m = new THREE.Mesh(ctx.trees[k], ctx.foliage), sc = 0.55 + rnd() * 0.4;
      m.position.set((rnd() - 0.5) * 1.05, 0, (rnd() - 0.5) * 1.05); m.rotation.y = rnd() * 6.28; m.scale.setScalar(sc); m.castShadow = m.receiveShadow = true; s.add(m);
    }
    for (let i = 0; i < 10; i++) { const x = (rnd() - 0.5) * 1.1, z = (rnd() - 0.5) * 1.1;
      post(s, kit.plain(0xe8dcc0, cond), 0.008, 0.05, x, 0.05, z); ball(s, kit.plain(col.getHex(), cond, { rough: 0.5 }), 0.022, x, 0.085, z, 0.6).castShadow = false; }
    box(s, kit.tex('planks', 0x7a5530, cond), 0.26, 0.03, 0.08, 0.3, 0.1, 0.5);
    for (const o of [-0.09, 0.09]) box(s, kit.tex('stone', 0x8a8074, cond), 0.03, 0.09, 0.07, 0.3 + o, 0.05, 0.5);
  },
  aqueduct(s, ctx, p, col, cond, rnd) {
    const { kit } = ctx, st = kit.tex('stone', C(0xbfb39a).lerp(col, 0.15).getHex(), cond);
    const sh = new THREE.Shape(); sh.moveTo(0, 0); sh.lineTo(0.5, 0); sh.lineTo(0.5, 0.82); sh.lineTo(0, 0.82); sh.lineTo(0, 0);
    const hole = new THREE.Path(); hole.moveTo(0.08, 0); hole.lineTo(0.08, 0.42); hole.absarc(0.25, 0.42, 0.17, Math.PI, 0, true); hole.lineTo(0.42, 0); hole.lineTo(0.08, 0); sh.holes.push(hole);
    for (let i = 0; i < 3; i++) { const g = new THREE.ExtrudeGeometry(sh, { depth: 0.3, bevelEnabled: false, curveSegments: 10 }); uvk(g, 1.6, 1.6);
      part(s, g, st, -0.75 + i * 0.5, 0, -0.15); }
    box(s, st, 1.56, 0.1, 0.36, 0, 0.87, 0);
    for (const z of [-1, 1]) box(s, st, 1.56, 0.07, 0.04, 0, 0.965, z * 0.16);
    box(s, kit.plain(0x2d86b8, 100, { rough: 0.03, tr: 0.88 }), 1.5, 0.05, 0.26, 0, 0.93, 0);
  },
  library(s, ctx, p, col, cond, rnd) {
    const { kit, fx } = ctx, wall = kit.tex('stone', C(0xd2c8b0).lerp(col, 0.2).getHex(), cond);
    box(s, kit.tex('stone', 0xa89e8c, cond), 1.24, 0.08, 0.94, 0, 0.04, 0);
    box(s, wall, 1.1, 0.64, 0.8, 0, 0.4, 0);
    for (let i = 0; i < 4; i++) { const x = -0.42 + i * 0.28; part(s, new THREE.CylinderGeometry(0.04, 0.048, 0.6, 12), wall, x, 0.4, 0.46); box(s, wall, 0.11, 0.03, 0.11, x, 0.72, 0.46); }
    box(s, wall, 1.2, 0.05, 0.16, 0, 0.75, 0.46);
    part(s, gableRoof(1.2, 0.32, 0.16), kit.tex('tiles', 0x6c3a26, cond), 0, 0.77, 0.46);
    const dome = part(s, new THREE.SphereGeometry(0.34, 22, 12, 0, Math.PI * 2, 0, Math.PI / 2), kit.plain(0x5aa08a, cond, { metal: 0.55, rough: 0.4 }), 0, 0.72, -0.08);
    part(s, new THREE.CylinderGeometry(0.36, 0.36, 0.06, 22), wall, 0, 0.72, -0.08); ball(s, kit.plain(0xe0b23a, 100, { metal: 0.9, rough: 0.3 }), 0.035, 0, 1.08, -0.08);
    doorAt(s, kit, 0, 0.08, 0.402, 0.18, 0.3, 0, cond, true);
    for (const x of [-0.36, 0.36]) windowAt(s, kit, x, 0.4, 0.402, 0.1, 0.22, 0, false, cond);
    for (const sx of [-1, 1]) for (const z of [-0.2, 0.2]) windowAt(s, kit, sx * 0.555, 0.4, z, 0.1, 0.22, sx * Math.PI / 2, false, cond);
    for (let i = 0; i < 4; i++) box(s, kit.plain([0x8a2a2a, 0x2a5a8a, 0x2a6a3a, 0xa87a2a][i], 100), 0.09, 0.025, 0.06, 0.55, 0.055 + i * 0.026, 0.3, 0.2);
    void dome; void fx;
  },
  wall(s, ctx, p, col, cond, rnd) {
    const { kit, fx } = ctx, st = kit.tex('stone', C(0xb8ab92).lerp(col, 0.12).getHex(), cond);
    box(s, st, 1.9, 0.6, 0.26, 0, 0.3, 0); box(s, kit.tex('stone', 0x8f8676, cond), 1.96, 0.08, 0.32, 0, 0.04, 0);
    for (let i = -4; i <= 4; i += 2) box(s, st, 0.18, 0.16, 0.28, i * 0.21, 0.68, 0);
    for (const x of [-0.95, 0, 0.95]) { box(s, st, 0.2, 0.7, 0.34, x, 0.35, 0); }
    bannerAt(s, kit, col.getHex(), 0.16, 0.3, 0, 0.6, 0.16, 0);
    torchAt(s, kit, fx, p.id, 0.5, 0.6, 0.17);
  },
  road(s, ctx, p, col, cond, rnd) {
    const { kit } = ctx;
    box(s, kit.tex('cobble', C(0xb0a48c).lerp(col, 0.15).getHex(), cond), 1.9, 0.05, 0.6, 0, 0.022, 0);
    for (const z of [-1, 1]) for (let i = -4; i <= 4; i++) box(s, kit.tex('stone', 0x9a9080, cond), 0.2, 0.07, 0.07, i * 0.22, 0.03, z * 0.32);
    for (let i = 0; i < 5; i++) ball(s, kit.plain(0x8a7a60, cond), 0.03 + rnd() * 0.03, (rnd() - 0.5) * 1.7, 0.055, (rnd() - 0.5) * 0.5, 0.5).castShadow = false;
  },
};

export function makeCivic(g, ctx, p, spec, rnd, cond) {
  const s = new THREE.Group(), k = spec.scale || 1, col = new THREE.Color(spec.color || '#c9b98f');
  s.scale.setScalar(k); g.add(s);
  shapes[spec.shape](s, ctx, p, col, cond, rnd);
  g.userData.float = g.userData.float || s.userData.float;
}
