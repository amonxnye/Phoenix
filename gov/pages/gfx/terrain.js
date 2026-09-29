/* Phoenix 3D — the land. A single smooth heightfield: the 24×16 playfield is gentle and
   buildable; past its edge the world rises into wooded hills, then mountains, with a lake on
   the far side. Colour is painted per vertex from what the simulation says lives on each tile
   (forest floor, tilled berry soil, ore-bearing rock, the town's cobbles, worn footpaths). */
import * as THREE from 'three';
import { thash, fbm, ridged, smooth, lerp, clamp } from './util.js';

export const WATER_Y = -0.02;
const C = (h) => new THREE.Color(h);
const COL = {
  grassA: C(0x3f5f1f), grassB: C(0x668430), dry: C(0x93893f), forestFloor: C(0x24301a), soil: C(0x4d3722),
  ore: C(0x66553a), cobble: C(0x958a72), path: C(0x71583a), sand: C(0xa39a70), wet: C(0x4b4b39), bed: C(0x33443b),
  hillForest: C(0x21351a), rock: C(0x6d675d), alpine: C(0x7b7562), snow: C(0xeef2f8), meadow: C(0x76913a),
};

/** the road each ground's walkers follow, in world coordinates (same shape as before) */
export function buildCurves(map) {
  const MW = map.w, MH = map.h, wx = (x) => x - MW / 2 + 0.5, wz = (y) => y - MH / 2 + 0.5;
  const [tx, ty] = map.town, from = new THREE.Vector3(wx(tx), 0, wz(ty)), curves = {};
  for (const res in map.grounds) {
    const [gx, gy] = map.grounds[res], to = new THREE.Vector3(wx(gx), 0, wz(gy));
    const mid = from.clone().lerp(to, 0.5);
    const perp = new THREE.Vector3(-(to.z - from.z), 0, to.x - from.x).normalize().multiplyScalar((thash(gx, gy) - 0.5) * 4.5);
    curves[res] = new THREE.QuadraticBezierCurve3(from.clone(), mid.add(perp), to.clone());
  }
  return curves;
}

export function makeField(map, curves) {
  const MW = map.w, MH = map.h, hx = MW / 2, hz = MH / 2, terr = map.terrain || [];
  const wx = (x) => x - hx + 0.5, wz = (y) => y - hz + 0.5;
  const cls = new Array(MW * MH).fill(''), water = [];
  for (const t of terr) { cls[t.y * MW + t.x] = t.cls; if (t.cls === 'water') water.push([wx(t.x), wz(t.y)]); }
  const town = new THREE.Vector3(wx(map.town[0]), 0, wz(map.town[1]));
  const PAD_H = 0.15;

  // footpath samples in a spatial hash
  const pathHash = new Map(), pathPts = [];
  for (const res in curves) {
    const c = curves[res], n = Math.ceil(c.getLength() / 0.12);
    for (let i = 0; i <= n; i++) {
      const p = c.getPoint(i / n); pathPts.push([p.x, p.z]);
      const k = Math.floor(p.x) + ',' + Math.floor(p.z);
      (pathHash.get(k) || pathHash.set(k, []).get(k)).push([p.x, p.z]);
    }
  }
  function pathDist(x, z) {
    let best = 9; const cx = Math.floor(x), cz = Math.floor(z);
    for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) {
      const a = pathHash.get((cx + i) + ',' + (cz + j)); if (!a) continue;
      for (const p of a) best = Math.min(best, Math.hypot(p[0] - x, p[1] - z));
    }
    return best;
  }
  function tileWeights(x, z, out) {           // class weights from the 3×3 tiles around (x,z)
    out.forest = out.berries = out.gold = out.water = 0;
    const tx = Math.floor(x + hx), tz = Math.floor(z + hz);
    for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) {
      const X = tx + i, Y = tz + j; if (X < 0 || Y < 0 || X >= MW || Y >= MH) continue;
      const c = cls[Y * MW + X]; if (!c) continue;
      const d = Math.max(Math.abs(x - wx(X)), Math.abs(z - wz(Y)));
      const w = 1 - smooth(0.34, 0.9, d);
      if (c === 'forest') out.forest = Math.max(out.forest, w); else if (c === 'berries') out.berries = Math.max(out.berries, w);
      else if (c === 'gold_seam') out.gold = Math.max(out.gold, w);
    }
  }

  function heightAt(x, z) {
    const dx = Math.max(0, Math.abs(x) - hx), dz = Math.max(0, Math.abs(z) - hz), d = Math.hypot(dx, dz);
    const rim = smooth(0, 16, d);
    let h = 0.13 + (fbm(x * 0.12 + 3.1, z * 0.12 - 1.7, 3, 1) - 0.5) * (0.16 + 0.9 * rim);
    if (d > 3) {
      h += smooth(4, 48, d) * (ridged(x * 0.034 + 7, z * 0.034 + 2, 4, 5) * 17 + fbm(x * 0.022, z * 0.022, 3, 8) * 12);
      h += smooth(46, 135, d) * ridged(x * 0.016 + 40, z * 0.016 + 9, 5, 11) * 52;
    }
    const lx = (x - 54) * 0.8, lz = z - 36, lk = smooth(9, 28, Math.hypot(lx, lz));   // the far lake
    h = h * lk + -2.6 * (1 - lk);
    if (water.length && Math.abs(x) < hx + 2 && Math.abs(z) < hz + 2) {              // the pond's bowl
      let pf = 0; for (const w of water) pf += smooth(1.45, 0.3, Math.hypot(x - w[0], z - w[1]));
      h -= 0.66 * smooth(0, 1, Math.min(1, pf));
    }
    const pd = Math.hypot(x - town.x, z - town.z);                                    // the keep's level pad
    if (pd < 3.6) h = lerp(h, PAD_H, smooth(3.6, 1.7, pd));
    return h;
  }

  const wts = { forest: 0, berries: 0, gold: 0, water: 0 };
  const tmp = new THREE.Color();
  /** paint one point of ground: writes linear colour into `out` */
  function groundColor(x, z, h, slope, out) {
    const dx = Math.max(0, Math.abs(x) - hx), dz = Math.max(0, Math.abs(z) - hz), d = Math.hypot(dx, dz);
    const n1 = fbm(x * 0.22, z * 0.22, 3, 21), n2 = fbm(x * 0.9, z * 0.9, 2, 22), dryK = smooth(0.6, 0.86, fbm(x * 0.07 + 9, z * 0.07, 3, 23));
    out.copy(COL.grassA).lerp(COL.grassB, n1).lerp(COL.dry, dryK * 0.5);
    if (n2 > 0.72) out.lerp(COL.meadow, 0.35);
    if (d < 1.5) {
      tileWeights(x, z, wts);
      if (wts.forest) out.lerp(COL.forestFloor, wts.forest * 0.85);
      if (wts.berries) { const row = 0.5 + 0.5 * Math.sin(z * 24); out.lerp(tmp.copy(COL.soil).multiplyScalar(0.8 + row * 0.35), wts.berries * 0.7); }
      if (wts.gold) out.lerp(COL.ore, wts.gold * 0.8);
    }
    const pd = Math.hypot(x - town.x, z - town.z);
    if (pd < 2.8) out.lerp(tmp.copy(COL.cobble).multiplyScalar(0.75 + n2 * 0.4), 1 - smooth(1.5, 2.8, pd));
    const pth = pathDist(x, z);
    if (pth < 0.4) out.lerp(tmp.copy(COL.path).multiplyScalar(0.8 + n2 * 0.4), (1 - smooth(0.1, 0.4, pth)) * 0.9);
    // shore and lake bed
    if (h < WATER_Y + 0.14) {
      const s = smooth(WATER_Y + 0.14, WATER_Y - 0.02, h); out.lerp(COL.sand, s * 0.9);
      out.lerp(COL.wet, smooth(WATER_Y + 0.02, WATER_Y - 0.12, h) * 0.9);
      out.lerp(COL.bed, smooth(WATER_Y - 0.15, WATER_Y - 0.7, h));
    }
    // beyond the playfield: wooded hills, bare rock, alpine, snow
    if (d > 4) {
      const hf = smooth(4, 14, d) * (1 - smooth(16, 30, h)) * smooth(0.25, 0.5, fbm(x * 0.05 + 3, z * 0.05, 3, 31));
      out.lerp(COL.hillForest, hf * 0.8);
      out.lerp(COL.rock, smooth(0.42, 0.85, slope) * 0.9);
      out.lerp(COL.alpine, smooth(20, 32, h + n2 * 4) * 0.85);
      out.lerp(COL.snow, smooth(40, 50, h + (n1 - 0.5) * 8) * (1 - smooth(0.7, 1.2, slope) * 0.5));
    }
    return out;
  }

  return { MW, MH, hx, hz, wx, wz, heightAt, groundColor, pathDist, cls, town, water, PAD_H,
    inPlay: (x, z, m = 0) => Math.abs(x) < hx + m && Math.abs(z) < hz + m,
    slopeAt: (x, z) => { const e = 0.35; return Math.hypot(heightAt(x + e, z) - heightAt(x - e, z), heightAt(x, z + e) - heightAt(x, z - e)) / (2 * e); } };
}

/** the terrain mesh: dense under the town, coarser toward the horizon */
export function buildTerrain(field, T, quality = 1) {
  const N = quality >= 1 ? 256 : 176, R = 118, a = 0.3, W = N + 1;
  const wmap = (s) => R * (a * s + (1 - a) * s * s * s);
  const pos = new Float32Array(W * W * 3), col = new Float32Array(W * W * 3), uv = new Float32Array(W * W * 2), c = new THREE.Color();
  for (let j = 0; j < W; j++) for (let i = 0; i < W; i++) {
    const x = wmap(i / N * 2 - 1), z = wmap(j / N * 2 - 1), h = field.heightAt(x, z), k = j * W + i;
    pos[k * 3] = x; pos[k * 3 + 1] = h; pos[k * 3 + 2] = z; uv[k * 2] = x / 9; uv[k * 2 + 1] = z / 9;
  }
  const idx = new Uint32Array(N * N * 6);
  let q = 0;
  for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
    const a0 = j * W + i, b0 = a0 + 1, c0 = a0 + W, d0 = c0 + 1;
    idx[q++] = a0; idx[q++] = c0; idx[q++] = b0; idx[q++] = b0; idx[q++] = c0; idx[q++] = d0;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  geo.setIndex(new THREE.BufferAttribute(idx, 1));
  geo.computeVertexNormals();
  const nrm = geo.attributes.normal;
  for (let k = 0; k < W * W; k++) {
    field.groundColor(pos[k * 3], pos[k * 3 + 2], pos[k * 3 + 1], Math.sqrt(Math.max(0, 1 - nrm.getY(k) * nrm.getY(k))) / Math.max(0.2, nrm.getY(k)), c);
    col[k * 3] = c.r; col[k * 3 + 1] = c.g; col[k * 3 + 2] = c.b;
  }
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, map: T.ground.map, normalMap: T.ground.normalMap,
    normalScale: new THREE.Vector2(0.55, 0.55), roughness: 1, metalness: 0 });
  const mesh = new THREE.Mesh(geo, mat); mesh.receiveShadow = true; mesh.userData.terrain = true;
  return mesh;
}
