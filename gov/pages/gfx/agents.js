/* Phoenix 3D — the people. Every agent the simulation reports is a walking figure with
   swinging limbs, dressed and equipped for its work: an axe for wood, a basket for food, a
   pickaxe for gold, a banner for the herald. They walk the worn road to their ground, work
   it with a job-specific motion, and carry the haul home. A '?' floats over any agent waiting
   on a human decision. Nothing here decides anything; it draws what the record says. */
import * as THREE from 'three';
import { srand, strSeed, lerp, clamp, tinted } from './util.js';

const RES_HEX = { food: 0xd8564a, wood: 0xb5793a, gold: 0xe0b23a };
const BLUE = 0x6f9bff, GREEN = 0x8ec86a;
const SKIN = [0xf1c7a0, 0xe3ad82, 0xc98d64, 0x9b6440, 0x6f4429];
const HAIR = [0x2a1c12, 0x4a3020, 0x7a5030, 0xc8a05a, 0x9a9a9a, 0x1a1a1a];

let QTEX = null;
function questionTexture() {
  if (QTEX) return QTEX;
  const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
  g.font = 'bold 46px monospace'; g.textAlign = 'center'; g.fillStyle = '#ffd766'; g.shadowColor = '#ffb020'; g.shadowBlur = 10; g.fillText('?', 32, 48);
  QTEX = new THREE.CanvasTexture(c); QTEX.colorSpace = THREE.SRGBColorSpace; return QTEX;
}

/** a limb pivoting at its top: returns the pivot group */
function limb(parent, geo, mat, x, y, z, len) {
  const piv = new THREE.Group(); piv.position.set(x, y, z);
  const m = new THREE.Mesh(geo, mat); m.position.y = -len / 2; m.castShadow = true; piv.add(m); parent.add(piv); return piv;
}

export function makeFigure(kit, { res, herald, uid }) {
  const rnd = srand(strSeed(uid)), root = new THREE.Group(), body = new THREE.Group(); root.add(body);
  const tunic = herald ? BLUE : (RES_HEX[res] ? new THREE.Color(RES_HEX[res]).lerp(new THREE.Color(0x5a4a3a), 0.45).getHex() : GREEN);
  const skin = kit.plain(SKIN[(rnd() * SKIN.length) | 0], 100, { rough: 0.7 }), cloth = kit.plain(tunic, 100, { rough: 0.85 });
  const pants = kit.plain(0x3a2f26, 100, { rough: 0.9 }), leather = kit.plain(0x4a3220, 100, { rough: 0.8 });
  const H = herald ? 1.12 : 1;
  const legG = new THREE.CapsuleGeometry(0.028, 0.14, 3, 8), armG = new THREE.CapsuleGeometry(0.022, 0.12, 3, 8);
  const lL = limb(body, legG, pants, -0.035, 0.22 * H, 0, 0.19), lR = limb(body, legG, pants, 0.035, 0.22 * H, 0, 0.19);
  for (const l of [lL, lR]) { const b = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.03, 0.08), leather); b.position.set(0, -0.2, 0.015); b.castShadow = true; l.add(b); }
  const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.058, 0.13, 4, 10), cloth); torso.position.y = 0.33 * H; torso.scale.set(1, 1, 0.8); torso.castShadow = true; body.add(torso);
  const belt = new THREE.Mesh(new THREE.CylinderGeometry(0.062, 0.062, 0.02, 10), leather); belt.position.y = 0.27 * H; belt.scale.z = 0.8; body.add(belt);
  const head = new THREE.Group(); head.position.y = 0.475 * H; body.add(head);
  const skull = new THREE.Mesh(new THREE.SphereGeometry(0.05, 14, 10), skin); skull.castShadow = true; head.add(skull);
  const hairM = new THREE.Mesh(new THREE.SphereGeometry(0.053, 12, 8, 0, Math.PI * 2, 0, Math.PI * 0.55), kit.plain(HAIR[(rnd() * HAIR.length) | 0], 100, { rough: 0.9 })); hairM.position.y = 0.006; hairM.rotation.x = -0.25; head.add(hairM);
  for (const s of [-1, 1]) { const e = new THREE.Mesh(new THREE.SphereGeometry(0.008, 6, 5), kit.plain(0x141414)); e.position.set(s * 0.02, 0.005, 0.045); head.add(e); }
  const aL = limb(body, armG, cloth, -0.078, 0.4 * H, 0, 0.16), aR = limb(body, armG, cloth, 0.078, 0.4 * H, 0, 0.16);
  for (const a of [aL, aR]) { const h = new THREE.Mesh(new THREE.SphereGeometry(0.02, 8, 6), skin); h.position.y = -0.17; a.add(h); }
  let tool = null, pack = null;
  if (herald) {                                                     // cape, pole and pennant
    const cape = new THREE.Mesh(new THREE.ConeGeometry(0.09, 0.3, 10, 1, true), kit.cloth('#2a4aa8')); cape.position.set(0, 0.28, -0.03); cape.rotation.x = 0.12; body.add(cape);
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.007, 0.007, 0.7, 6), leather); pole.position.set(0.11, 0.36, 0.02); body.add(pole);
    const fl = new THREE.Mesh(new THREE.PlaneGeometry(0.16, 0.09), kit.cloth('#e0b23a')); fl.position.set(0.19, 0.62, 0.02); body.add(fl);
    const hat = new THREE.Mesh(new THREE.ConeGeometry(0.06, 0.09, 8), kit.plain(0x1c2c6a)); hat.position.y = 0.075; head.add(hat);
  } else if (res === 'wood') {
    tool = new THREE.Group(); const hd = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 0.26, 6), leather); hd.position.y = -0.03; tool.add(hd);
    const bl = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.05, 0.01), kit.plain(0x9da3aa, 100, { metal: 0.85, rough: 0.3 })); bl.position.set(0.025, 0.08, 0); tool.add(bl);
    tool.position.set(0, -0.17, 0.02); aR.add(tool); tool.rotation.x = 1.2;
  } else if (res === 'gold') {
    tool = new THREE.Group(); const hd = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 0.28, 6), leather); tool.add(hd);
    const bl = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.014, 0.012), kit.plain(0x8d9096, 100, { metal: 0.9, rough: 0.35 })); bl.position.y = 0.13; tool.add(bl);
    tool.position.set(0, -0.15, 0.02); aR.add(tool); tool.rotation.x = 1.15;
  } else if (res === 'food') {
    const basket = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.038, 0.06, 10), kit.tex('thatch', 0xc7a866, 100)); basket.position.set(0, -0.19, 0.03); aL.add(basket);
    const hat = new THREE.Mesh(new THREE.ConeGeometry(0.1, 0.045, 12), kit.tex('thatch', 0xd8b878, 100)); hat.position.y = 0.045; head.add(hat);
  }
  if (!herald) {                                                    // the haul, carried home on the back
    pack = new THREE.Group(); pack.position.set(0, 0.34 * H, -0.075); body.add(pack);
    const hex = RES_HEX[res] ?? 0xcccccc;
    if (res === 'wood') for (let i = 0; i < 3; i++) { const l = new THREE.Mesh(new THREE.CylinderGeometry(0.017, 0.017, 0.16, 6), kit.tex('planks', 0x8a6a40, 100)); l.rotation.z = Math.PI / 2; l.position.set(0, -0.02 + i * 0.03, 0); l.rotation.y = (i - 1) * 0.3; pack.add(l); }
    else { const sack = new THREE.Mesh(new THREE.SphereGeometry(0.06, 10, 8), kit.plain(hex, 100, { rough: 0.7, em: hex, ei: res === 'gold' ? 0.25 : 0.05 })); sack.scale.set(1, 1.1, 0.8); pack.add(sack); }
    pack.visible = false;
  }
  const q = new THREE.Sprite(new THREE.SpriteMaterial({ map: questionTexture(), transparent: true, depthWrite: false, depthTest: false })); q.scale.setScalar(0.34); q.position.y = 0.85 * H; q.visible = false; q.renderOrder = 10; q.userData.noAO = true; root.add(q);
  root.scale.setScalar(herald ? 1.0 : 0.95);
  root.userData = { body, lL, lR, aL, aR, head, tool, pack, q, herald, H };
  return root;
}

export class Agents {
  constructor(scene, kit, field, fx) { this.kit = kit; this.field = field; this.fx = fx; this.root = new THREE.Group(); scene.add(this.root); this.ents = new Map(); this.beaconOn = false; this.curves = {}; this.town = null; }
  setCurves(curves) { this.curves = curves; }
  setTown(v) { this.town = v; }
  sync(agents) {
    const seen = new Set(); let any = false;
    for (const a of agents) {
      seen.add(a.uid); any = any || !!a.pending;
      const res = (a.task || '').startsWith('gather') ? ((a.task.split(' ')[1]) || 'food') : null, herald = a.role === 'herald' || a.uid.startsWith('herald');
      let e = this.ents.get(a.uid);
      if (!e || e.res !== res || e.herald !== herald) {
        if (e) this.root.remove(e.g);
        const r = srand(strSeed(a.uid));
        e = { g: makeFigure(this.kit, { res, herald, uid: a.uid }), res, herald, off: r() * 2, lat: (r() - 0.5) * 0.4, speed: 0.07 + r() * 0.025, spot: r(), foot: 0, uid: a.uid };
        this.root.add(e.g); this.ents.set(a.uid, e);
      }
      e.status = a.status; e.pending = a.pending; e.task = a.task; e.role = a.role;
      e.g.traverse((o) => { o.userData.agent = { uid: a.uid, task: a.task, status: a.status, role: a.role || (herald ? 'herald' : 'villager'), pending: a.pending }; });
    }
    for (const [uid, e] of this.ents) if (!seen.has(uid)) { this.root.remove(e.g); this.ents.delete(uid); }
    this.beaconOn = any; return any;
  }
  update(t, dt) {
    const F = this.field;
    for (const [, e] of this.ents) {
      const g = e.g, u = g.userData;
      if (e.herald) {                                             // the herald waits by the keep's gate
        const T = this.town || F.town, x = T.x + 1.05, z = T.z + 1.15, y = F.heightAt(x, z);
        g.position.set(x, y + Math.sin(t * 1.4) * 0.004, z); g.rotation.y = Math.sin(t * 0.4) * 0.3 - 0.4;
        u.aL.rotation.x = Math.sin(t * 1.1) * 0.04; u.aR.rotation.x = -0.1; u.head.rotation.y = Math.sin(t * 0.6) * 0.5;
        u.q.visible = !!e.pending; if (e.pending) u.q.position.y = 0.95 + Math.sin(t * 2.4) * 0.05; continue;
      }
      const curve = this.curves[e.res]; if (!curve) { g.visible = false; continue; } g.visible = true;
      if (e.status === 'running') {                               // walk the road out, carry the haul back
        const ph = (t * e.speed + e.off) % 2, out = ph < 1, k = out ? ph : 2 - ph;
        const p = curve.getPoint(k), tan = curve.getTangent(k), lat = e.lat * (out ? 1 : -1);
        const x = p.x + -tan.z * lat, z = p.z + tan.x * lat, gait = t * 7.5 + e.off * 9, sw = Math.sin(gait);
        g.position.set(x, F.heightAt(x, z) + Math.abs(Math.sin(gait)) * 0.012, z);
        const dir = out ? 1 : -1; g.rotation.y = Math.atan2(tan.x * dir, tan.z * dir);
        u.lL.rotation.x = sw * 0.75; u.lR.rotation.x = -sw * 0.75; u.aL.rotation.x = -sw * 0.6; u.aR.rotation.x = sw * 0.6;
        u.body.rotation.x = 0.06; u.body.rotation.z = Math.sin(gait * 0.5) * 0.03; u.head.rotation.y = 0;
        if (u.tool) u.tool.rotation.x = 1.2;
        if (u.pack) u.pack.visible = !out;
        if (Math.floor(gait / Math.PI) !== e.foot) { e.foot = Math.floor(gait / Math.PI); this.fx?.dust(x, F.heightAt(x, z), z); }
        u.q.visible = false;
      } else {                                                    // at the ground: work it, or wait
        const end = curve.getPoint(1), ang = e.spot * 6.28, r = 0.5 + e.spot * 0.45, x = end.x + Math.cos(ang) * r, z = end.z + Math.sin(ang) * r;
        g.position.set(x, F.heightAt(x, z), z); g.rotation.y = ang + Math.PI * 1.5 + Math.sin(t * 0.7 + e.off * 7) * 0.12;
        const wait = e.pending || e.status === 'awaiting_approval', w = t * 3.6 + e.off * 5;
        u.lL.rotation.x = u.lR.rotation.x = 0;
        if (e.res === 'wood' || e.res === 'gold') {                // chop / dig
          const s = Math.sin(w); u.aR.rotation.x = -1.4 + Math.max(0, s) * 2.1; u.aL.rotation.x = -0.9 + Math.max(0, s) * 1.4; u.body.rotation.x = 0.12 + Math.max(0, s) * 0.16;
          if (u.tool) u.tool.rotation.x = 1.2 - Math.max(0, s) * 0.5;
        } else { const s = 0.5 + 0.5 * Math.sin(w * 0.6); u.body.rotation.x = 0.45 * s; u.aR.rotation.x = -0.4 - 0.8 * s; u.aL.rotation.x = -0.3 - 0.4 * s; }
        u.head.rotation.y = wait ? Math.sin(t * 0.8 + e.off * 6) * 0.6 : 0; u.body.rotation.z = 0;
        if (wait) { u.aL.rotation.x = u.aR.rotation.x = -0.1 + Math.sin(t * 1.2 + e.off) * 0.04; u.body.rotation.x = 0; }
        if (u.pack) u.pack.visible = false;
        u.q.visible = wait; if (wait) u.q.position.y = 0.72 + Math.sin(t * 2.2 + e.off * 4) * 0.05;
      }
    }
  }
}

/* ── a small flock grazing the meadow ────────────────────────────────────────────── */
export class Flock {
  constructor(scene, kit, field, n = 6) {
    this.field = field; this.kit = kit; this.group = new THREE.Group(); scene.add(this.group); this.sheep = []; this.avoid = [];
    const rnd = srand(6060), wool = kit.plain(0xf1ede3, 100, { rough: 1 }), face = kit.plain(0x2a2622, 100, { rough: 0.9 });
    for (let i = 0; i < n; i++) {
      const g = new THREE.Group(), b = new THREE.Mesh(new THREE.IcosahedronGeometry(0.075, 2), wool); b.scale.set(1.15, 0.9, 1.5); b.position.y = 0.11; b.castShadow = true; g.add(b);
      const h = new THREE.Group(); h.position.set(0, 0.14, 0.12); const hm = new THREE.Mesh(new THREE.SphereGeometry(0.035, 8, 6), face); hm.scale.set(0.9, 1, 1.25); hm.castShadow = true; h.add(hm); g.add(h);
      const legs = [];
      for (const [x, z] of [[-0.04, 0.07], [0.04, 0.07], [-0.04, -0.07], [0.04, -0.07]]) { const l = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 0.08, 5), face); l.position.set(x, 0.04, z); g.add(l); legs.push(l); }
      const x = (rnd() - 0.5) * 16, z = (rnd() - 0.5) * 10;
      g.position.set(x, field.heightAt(x, z), z); this.sheep.push({ g, h, legs, tx: x, tz: z, wait: rnd() * 4, ph: rnd() * 6.28 }); this.group.add(g);
    }
  }
  setAvoid(list) { this.avoid = list; }
  free(x, z) {
    const F = this.field;
    if (Math.abs(x) > F.hx - 0.6 || Math.abs(z) > F.hz - 0.6 || F.heightAt(x, z) < -0.0) return false;
    for (const a of this.avoid) if (Math.hypot(a[0] - x, a[1] - z) < a[2]) return false;
    return true;
  }
  update(t, dt) {
    const F = this.field;
    for (const s of this.sheep) {
      const dx = s.tx - s.g.position.x, dz = s.tz - s.g.position.z, d = Math.hypot(dx, dz);
      if (d < 0.08) {
        s.wait -= dt; s.h.rotation.x = 0.6 + Math.sin(t * 2 + s.ph) * 0.15;                    // grazing
        if (s.wait <= 0) { for (let k = 0; k < 12; k++) { const x = s.g.position.x + (Math.random() - 0.5) * 3, z = s.g.position.z + (Math.random() - 0.5) * 3; if (this.free(x, z)) { s.tx = x; s.tz = z; break; } } s.wait = 2 + Math.random() * 6; }
      } else {
        const sp = 0.16 * dt; s.g.position.x += dx / d * Math.min(sp, d); s.g.position.z += dz / d * Math.min(sp, d);
        s.g.rotation.y = lerp(s.g.rotation.y, Math.atan2(dx, dz), 0.1); s.h.rotation.x = 0;
        s.legs.forEach((l, i) => { l.rotation.x = Math.sin(t * 6 + (i % 2 ? Math.PI : 0) + s.ph) * 0.5; });
      }
      s.g.position.y = F.heightAt(s.g.position.x, s.g.position.z);
    }
  }
}
