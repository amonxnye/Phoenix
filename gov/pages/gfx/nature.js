/* Phoenix 3D — living nature: wind-swayed pines, oaks and birches, meadow grass and
   wildflowers, boulders, reeds, berry bushes and gold-bearing crystal. Everything the
   simulation owns (forest stock, berry bushes, ore seams) is drawn from its live tiles;
   everything decorative is seeded so the same world looks the same on every load. */
import * as THREE from 'three';
import { mergeGeometries, mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { srand, thash, strSeed, clamp, lerp, smooth, tinted, jitter, normalized, fbm, vnoise } from './util.js';
import { WATER_Y } from './terrain.js';

/* ── wind: one shared clock, injected into any material that should sway ────────── */
export const WIND = { value: 0 };
export function windify(mat, amp = 1, grass = false) {
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.uWind = WIND;
    sh.vertexShader = 'uniform float uWind;\n' + sh.vertexShader.replace('#include <begin_vertex>', `
      #include <begin_vertex>
      #ifdef USE_INSTANCING
        vec3 ip = vec3(instanceMatrix[3][0], 0.0, instanceMatrix[3][2]);
      #else
        vec3 ip = vec3(0.0);
      #endif
      float hgt = max(position.y, 0.0);
      float ph = uWind * ${grass ? '2.6' : '1.7'} + ip.x * 0.6 + ip.z * 0.8;
      float gust = sin(uWind * 0.45 + ip.x * 0.13 + ip.z * 0.09) * 0.5 + 0.5;
      float sw = (sin(ph) * 0.6 + sin(ph * 2.3 + 1.3) * 0.4) * (0.012 + 0.03 * gust) * ${amp.toFixed(2)} * hgt * hgt;
      ${grass ? `
      #ifdef USE_INSTANCING
        float sxs = max(0.02, length(instanceMatrix[0].xyz));
      #else
        float sxs = 1.0;
      #endif
      transformed.x += sw * 0.6 / sxs; transformed.z += sw * 1.4;` : 'transformed.x += sw; transformed.z += sw * 0.6;'}
    `);
  };
  mat.customProgramCacheKey = () => 'wind' + amp + (grass ? 'g' : '');
  return mat;
}

/* ── tree geometry: merged once, drawn instanced ─────────────────────────────────── */
const col = (hex) => new THREE.Color(hex);
function paintY(geo, fn, seed = 1) {           // colour by absolute height, with a little noise
  const p = geo.attributes.position, c = new Float32Array(p.count * 3), r = srand(seed), t = new THREE.Color();
  for (let i = 0; i < p.count; i++) { fn(p.getY(i), t, r()); c[i * 3] = t.r; c[i * 3 + 1] = t.g; c[i * 3 + 2] = t.b; }
  geo.setAttribute('color', new THREE.BufferAttribute(c, 3)); return geo;
}
/** a lumpy, smooth-shaded clump of leaves: welded sphere, displaced by layered noise */
function blob(r, sx, sy, sz, x, y, z, detail, seed) {
  let g = new THREE.IcosahedronGeometry(r, detail + 1); g.deleteAttribute('normal'); g.deleteAttribute('uv');
  g = mergeVertices(g);
  const p = g.attributes.position, uv = new Float32Array(p.count * 2);
  for (let i = 0; i < p.count; i++) {
    const vx = p.getX(i), vy = p.getY(i), vz = p.getZ(i), l = Math.hypot(vx, vy, vz) || 1;
    const k = 1 + (vnoise(vx * 9 + seed, vz * 9 + vy * 7, seed) - 0.5) * 0.55 + (vnoise(vx * 22, vy * 22 + seed, vz * 22) - 0.5) * 0.18;
    p.setXYZ(i, vx / l * r * k * sx + x, vy / l * r * k * sy + y, vz / l * r * k * sz + z);
    uv[i * 2] = Math.atan2(vz, vx) / 6.283 * 3; uv[i * 2 + 1] = vy / l * 1.5;
  }
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2)); g.computeVertexNormals();
  return g;
}
export function treeGeometries(detail = 1) {
  const out = {};
  // pine: bark + five drooping tiers
  { const parts = [], tr = new THREE.CylinderGeometry(0.03, 0.06, 0.6, 6, 1); tr.translate(0, 0.3, 0);
    paintY(tr, (y, t) => t.copy(col(0x4a3120)).multiplyScalar(0.8 + y * 0.3), 3); parts.push(normalized(tr));
    for (let i = 0; i < 5; i++) {
      const k = i / 4, r = 0.36 * (1 - k * 0.74) + 0.03, h = 0.4 * (1 - k * 0.32) + 0.06, y = 0.36 + k * 0.78 + h / 2 * 0.9;
      const cone = new THREE.ConeGeometry(r, h, detail ? 12 : 8, detail ? 3 : 1); cone.translate(0, y, 0);
      jitter(cone, 0.05 + r * 0.14, 7 + i);
      paintY(cone, (yy, t, n) => t.copy(col(0x16341a)).lerp(col(0x3f7530), clamp((yy - 0.3) / 1.0)).multiplyScalar(0.86 + n * 0.28), 11 + i);
      parts.push(normalized(cone));
    }
    out.pine = mergeGeometries(parts, false); }
  // oak: thick trunk, broad rounded crown of leaf clumps
  { const parts = [], tr = new THREE.CylinderGeometry(0.045, 0.08, 0.62, 7, 1); tr.translate(0, 0.31, 0);
    paintY(tr, (y, t) => t.copy(col(0x5a4128)).multiplyScalar(0.75 + y * 0.3), 5); parts.push(normalized(tr));
    const rnd = srand(77), n = 7;
    for (let i = 0; i < n; i++) {
      const a = i / n * Math.PI * 2, rr = i ? 0.24 : 0, y = 0.86 + (i ? (rnd() - 0.5) * 0.22 : 0.2);
      const b = blob(0.27 + rnd() * 0.07, 1, 0.8 + rnd() * 0.15, 1, Math.cos(a) * rr, y, Math.sin(a) * rr, detail ? 1 : 0, 20 + i);
      paintY(b, (yy, t, nz) => t.copy(col(0x1f4014)).lerp(col(0x5e8a2c), clamp((yy - 0.6) / 0.7)).multiplyScalar(0.8 + nz * 0.3), 30 + i);
      parts.push(normalized(b));
    }
    out.oak = mergeGeometries(parts, false); }
  // birch: pale slender trunk, light airy crown
  { const parts = [], tr = new THREE.CylinderGeometry(0.02, 0.04, 0.9, 6, 1); tr.translate(0, 0.45, 0);
    paintY(tr, (y, t, n) => { t.copy(col(0xe9e4d8)); if (n > 0.86) t.multiplyScalar(0.28); }, 9); parts.push(normalized(tr));
    const rnd = srand(91);
    for (let i = 0; i < 5; i++) {
      const b = blob(0.2 + rnd() * 0.05, 1, 1.15, 1, (rnd() - 0.5) * 0.24, 0.85 + i * 0.13, (rnd() - 0.5) * 0.24, detail ? 1 : 0, 50 + i);
      paintY(b, (yy, t, nz) => t.copy(col(0x3f6a1e)).lerp(col(0x8fac3c), clamp((yy - 0.7) / 0.7)).multiplyScalar(0.85 + nz * 0.3), 60 + i);
      parts.push(normalized(b));
    }
    out.birch = mergeGeometries(parts, false); }
  // a low bush / undergrowth clump
  { const parts = [], rnd = srand(31);
    for (let i = 0; i < 5; i++) {
      const b = blob(0.13 + rnd() * 0.05, 1, 0.75, 1, (rnd() - 0.5) * 0.24, 0.1 + rnd() * 0.05, (rnd() - 0.5) * 0.24, 1, 80 + i);
      paintY(b, (yy, t, nz) => t.copy(col(0x224414)).lerp(col(0x5a8428), clamp(yy / 0.3)).multiplyScalar(0.84 + nz * 0.3), 90 + i);
      parts.push(normalized(b));
    }
    out.bush = mergeGeometries(parts, false); }
  // a stump with a pale cut face
  { const g = new THREE.CylinderGeometry(0.075, 0.1, 0.13, 8, 1); g.translate(0, 0.065, 0);
    paintY(g, (y, t) => t.copy(y > 0.12 ? col(0xb08a55) : col(0x4c3524)), 2); out.stump = normalized(g); }
  return out;
}

/* ── grass blade: three quads' worth of tapered geometry ─────────────────────────── */
function bladeGeometry() {
  const p = [-0.5, 0, 0, 0.5, 0, 0, -0.36, 0.5, 0.03, 0.36, 0.5, 0.03, 0, 1, 0.08];
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(p), 3));
  g.setAttribute('normal', new THREE.BufferAttribute(new Float32Array([0, 1, 0.2, 0, 1, 0.2, 0, 1, 0.2, 0, 1, 0.2, 0, 1, 0.2]), 3));   // lit like the ground it grows from
  g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(10), 2));
  const c = []; for (const y of [0, 0, 0.5, 0.5, 1]) { const k = 0.32 + y * 0.9; c.push(k * 0.85, k, k * 0.6); }
  g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(c), 3));
  g.setIndex([0, 1, 2, 1, 3, 2, 2, 3, 4, 0, 2, 1, 1, 2, 3, 2, 4, 3]);          // both faces, one shared upward normal
  return g;
}

/* ── the scenery that never changes: meadow, boulders, far forests, reeds ─────────── */
/** hide the meadow under buildings, plazas and roads (discs are [x, z, radius]) */
export function maskGrass(blades, discs) {
  const orig = blades.userData.orig, arr = blades.instanceMatrix.array, n = blades.userData.full;
  if (!orig) return;
  for (let i = 0; i < n; i++) {
    const x = orig[i * 16 + 12], z = orig[i * 16 + 14]; let hide = false;
    for (const d of discs) if ((x - d[0]) * (x - d[0]) + (z - d[1]) * (z - d[1]) < d[2] * d[2]) { hide = true; break; }
    if (hide) for (let k = 0; k < 16; k++) arr[i * 16 + k] = 0; else for (let k = 0; k < 16; k++) arr[i * 16 + k] = orig[i * 16 + k];
  }
  blades.instanceMatrix.needsUpdate = true;
}

export function buildScenery(field, T, quality = 2) {
  const root = new THREE.Group(), dummy = new THREE.Object3D(), tint = new THREE.Color(), out = new THREE.Color();
  const { heightAt, groundColor, slopeAt, hx, hz } = field, MW = field.MW, MH = field.MH;
  const trees = treeGeometries(quality >= 2 ? 1 : 0);

  /* far forests on the hills (instanced, three species) */
  const foliage = windify(new THREE.MeshStandardMaterial({ vertexColors: true, map: T.leaf.map, normalMap: T.leaf.normalMap, normalScale: new THREE.Vector2(0.6, 0.6), roughness: 0.82, metalness: 0 }), 1);
  const nTrees = [900, 2100, 3600][quality];
  const spots = { pine: [], oak: [], birch: [] }, near = { pine: [], oak: [], birch: [] };
  { const rnd = srand(1717); let tries = 0;
    while ((spots.pine.length + spots.oak.length + spots.birch.length + near.pine.length + near.oak.length + near.birch.length) < nTrees && tries++ < nTrees * 9) {
      const a = rnd() * Math.PI * 2, r = 3 + Math.pow(rnd(), 0.62) * 105, x = Math.cos(a) * r * 1.35, z = Math.sin(a) * r;
      const dx = Math.max(0, Math.abs(x) - hx), dz = Math.max(0, Math.abs(z) - hz), d = Math.hypot(dx, dz);
      if (d < 2.2) continue;
      const h = heightAt(x, z); if (h < WATER_Y + 0.35 || h > 24) continue;
      const dens = smooth(0.3, 0.55, fbm(x * 0.05 + 3, z * 0.05, 3, 31)) * (1 - smooth(14, 24, h)) * smooth(2, 7, d);
      if (rnd() > dens) continue;
      if (slopeAt(x, z) > 0.75) continue;
      const alt = smooth(6, 20, h), kind = rnd() < 0.3 + alt * 0.5 ? 'pine' : rnd() < 0.7 ? 'oak' : 'birch';
      (d < 10 ? near : spots)[kind].push([x, h - 0.02, z, (1.2 + rnd() * 1.4) * (1 - alt * 0.3), rnd() * 6.28, 0.85 + rnd() * 0.3]);
    } }
  for (const kind of ['pine', 'oak', 'birch']) for (const [list, shadow] of [[spots[kind], false], [near[kind], true]]) {
    if (!list.length) continue;
    const im = new THREE.InstancedMesh(trees[kind], foliage, list.length);
    list.forEach((p, i) => {
      dummy.position.set(p[0], p[1], p[2]); dummy.rotation.set(0, p[4], 0); dummy.scale.setScalar(p[3]); dummy.updateMatrix();
      im.setMatrixAt(i, dummy.matrix); im.setColorAt(i, tint.setRGB(p[5], p[5], p[5] * (0.95 + thash(i, 3) * 0.1)));
    });
    im.castShadow = shadow; im.receiveShadow = true; im.computeBoundingSphere(); root.add(im);
  }

  /* meadow grass — thousands of swaying blades tinted from the ground under them */
  const nGrass = [14000, 38000, 70000][quality];
  const blades = new THREE.InstancedMesh(bladeGeometry(), windify(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9, metalness: 0 }), 1.6, true), nGrass);
  { const rnd = srand(2024); let n = 0, tries = 0;
    const X = hx + 9, Z = hz + 9;
    while (n < nGrass && tries++ < nGrass * 3) {
      const x = (rnd() * 2 - 1) * X, z = (rnd() * 2 - 1) * Z;
      const out2 = Math.max(0, Math.abs(x) - hx, Math.abs(z) - hz);
      if (out2 > 0 && rnd() > Math.exp(-out2 * 0.42)) continue;
      const h = heightAt(x, z); if (h < WATER_Y + 0.07) continue;
      if (field.pathDist(x, z) < 0.24) continue;
      if (Math.hypot(x - field.town.x, z - field.town.z) < 2.4) continue;
      const tx = Math.floor(x + hx), tz = Math.floor(z + hz), c = tx >= 0 && tz >= 0 && tx < MW && tz < MH ? field.cls[tz * MW + tx] : '';
      if (c === 'gold_seam') continue; if (c === 'forest' && rnd() < 0.6) continue;
      const s = c === 'berries' ? 0.5 : 1;
      const hgt = (0.05 + rnd() * 0.1) * s * (out2 > 0 ? 1.5 : 1), wid = 0.025 + rnd() * 0.02;
      dummy.position.set(x, h - 0.01, z); dummy.rotation.set((rnd() - 0.5) * 0.5, rnd() * 6.28, 0); dummy.scale.set(wid, hgt, 1); dummy.updateMatrix();
      blades.setMatrixAt(n, dummy.matrix);
      groundColor(x, z, h, 0, out); const k = 1.9 + rnd() * 0.7;
      blades.setColorAt(n, tint.setRGB(out.r * k * (0.85 + rnd() * 0.3), out.g * k * 1.2, out.b * k)); n++;
    }
    blades.count = n; blades.userData.full = n;
    blades.userData.orig = blades.instanceMatrix.array.slice(0, n * 16);          // for masking under buildings
    blades.computeBoundingSphere(); }
  blades.receiveShadow = true; root.add(blades);

  /* wildflowers */
  { const nF = [250, 600, 1100][quality], rnd = srand(555), pal = [0xffffff, 0xffe066, 0xb28cff, 0xff8fb1, 0xff7a4d, 0x8fd0ff];
    const head = new THREE.SphereGeometry(0.03, 6, 5); head.translate(0, 0.16, 0); tinted(head, 0xffffff);
    const stem = new THREE.CylinderGeometry(0.004, 0.006, 0.16, 4); stem.translate(0, 0.08, 0); tinted(stem, 0x3a6a24);
    const hm = new THREE.InstancedMesh(head, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.6, emissive: 0x111111 }), nF);
    const sm = new THREE.InstancedMesh(stem, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9 }), nF);
    let n = 0, tries = 0;
    while (n < nF && tries++ < nF * 8) {
      const x = (rnd() * 2 - 1) * (hx + 6), z = (rnd() * 2 - 1) * (hz + 6), h = heightAt(x, z);
      if (h < WATER_Y + 0.12 || field.pathDist(x, z) < 0.3 || Math.hypot(x - field.town.x, z - field.town.z) < 2.6) continue;
      if (fbm(x * 0.4, z * 0.4, 2, 61) < 0.5) continue;               // flowers grow in drifts
      dummy.position.set(x, h - 0.01, z); dummy.rotation.set(0, rnd() * 6.28, 0); dummy.scale.setScalar(0.7 + rnd() * 0.7); dummy.updateMatrix();
      hm.setMatrixAt(n, dummy.matrix); sm.setMatrixAt(n, dummy.matrix); hm.setColorAt(n, tint.setHex(pal[(rnd() * pal.length) | 0])); n++;
    }
    hm.count = sm.count = n; hm.computeBoundingSphere(); sm.computeBoundingSphere(); root.add(hm, sm); }

  /* rocks: pebbles on the plain, boulders on the slopes */
  { const rg = new THREE.IcosahedronGeometry(0.5, 1); rg.scale(1, 0.72, 0.9); jitter(rg, 0.34, 4);
    const rm = new THREE.MeshStandardMaterial({ map: T.rock.map, normalMap: T.rock.normalMap, roughnessMap: T.rock.roughnessMap, color: 0xcabfae, roughness: 1 });
    const spots2 = [], rnd = srand(808);
    for (let i = 0; i < [90, 200, 340][quality]; i++) {                                       // pebbles
      const x = (rnd() * 2 - 1) * (hx + 3), z = (rnd() * 2 - 1) * (hz + 3), h = heightAt(x, z);
      if (h < WATER_Y + 0.04 || field.pathDist(x, z) < 0.3) continue;
      spots2.push([x, h, z, 0.05 + rnd() * rnd() * 0.22, rnd() * 6.28]);
    }
    for (let i = 0; i < [70, 160, 260][quality]; i++) {                                       // boulders
      const a = rnd() * 6.28, r = 6 + rnd() * 80, x = Math.cos(a) * r * 1.3, z = Math.sin(a) * r, d = Math.max(Math.abs(x) - hx, Math.abs(z) - hz);
      const h = heightAt(x, z); if (d < 2 || h < WATER_Y + 0.2 || h > 38) continue;
      spots2.push([x, h - 0.05, z, 0.4 + rnd() * rnd() * 2.6, rnd() * 6.28]);
    }
    const im = new THREE.InstancedMesh(rg, rm, spots2.length);
    spots2.forEach((p, i) => { dummy.position.set(p[0], p[1] + p[3] * 0.25, p[2]); dummy.rotation.set(p[4] * 0.3, p[4], p[4] * 0.2);
      dummy.scale.set(p[3], p[3] * (0.8 + thash(i, 9) * 0.5), p[3] * (0.85 + thash(i, 4) * 0.3)); dummy.updateMatrix(); im.setMatrixAt(i, dummy.matrix);
      im.setColorAt(i, tint.setRGB(0.8 + thash(i, 1) * 0.35, 0.78 + thash(i, 2) * 0.3, 0.72 + thash(i, 5) * 0.3)); });
    im.castShadow = true; im.receiveShadow = true; im.computeBoundingSphere(); root.add(im); }

  /* reeds and lily pads round the pond */
  if (field.water.length) {
    const reedG = new THREE.CylinderGeometry(0.008, 0.014, 0.6, 4); reedG.translate(0, 0.3, 0); tinted(reedG, 0x6b8a3a, 0.4, 3);
    const tipG = new THREE.CylinderGeometry(0.02, 0.02, 0.1, 5); tipG.translate(0, 0.62, 0); tinted(tipG, 0x5a3a1c, 0.3, 4);
    const geo = mergeGeometries([normalized(reedG), normalized(tipG)], false);
    const rnd = srand(303), list = [];
    for (let i = 0; i < 120; i++) {
      const w = field.water[(rnd() * field.water.length) | 0], a = rnd() * 6.28, r = 0.9 + rnd() * 1.1, x = w[0] + Math.cos(a) * r, z = w[1] + Math.sin(a) * r;
      const h = heightAt(x, z); if (h < WATER_Y - 0.12 || h > WATER_Y + 0.22) continue;
      list.push([x, h, z, 0.6 + rnd() * 0.9, rnd() * 6.28]);
    }
    const im = new THREE.InstancedMesh(geo, windify(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.8 }), 1.2), list.length);
    list.forEach((p, i) => { dummy.position.set(p[0], p[1], p[2]); dummy.rotation.set(0, p[4], (thash(i, 2) - 0.5) * 0.2); dummy.scale.setScalar(p[3]); dummy.updateMatrix(); im.setMatrixAt(i, dummy.matrix); });
    im.computeBoundingSphere(); root.add(im);
    const padG = new THREE.CircleGeometry(0.16, 9); padG.rotateX(-Math.PI / 2);
    const pads = new THREE.InstancedMesh(padG, new THREE.MeshStandardMaterial({ color: 0x3f7a30, roughness: 0.6, side: THREE.DoubleSide }), 10);
    let n = 0; for (let i = 0; i < 60 && n < 10; i++) {
      const w = field.water[(rnd() * field.water.length) | 0], a = rnd() * 6.28, r = rnd() * 0.7, x = w[0] + Math.cos(a) * r, z = w[1] + Math.sin(a) * r;
      if (heightAt(x, z) > WATER_Y - 0.15) continue;
      dummy.position.set(x, WATER_Y + 0.012, z); dummy.rotation.set(0, rnd() * 6.28, 0); dummy.scale.setScalar(0.5 + rnd() * 0.5); dummy.updateMatrix(); pads.setMatrixAt(n++, dummy.matrix);
    }
    pads.count = n; pads.userData.noAO = true; root.add(pads);
  }
  root.userData = { blades, foliage, trees, setGrassDensity: (f) => { blades.count = Math.floor(blades.userData.full * f); } };
  return root;
}

/* ── the living tiles: forest stock, berry bushes, ore seams ─────────────────────── */
export class LiveNature {
  constructor(field, T, quality = 2) {
    this.field = field; this.group = new THREE.Group(); this.sig = ''; this.time = 0;
    this.trees = treeGeometries(1);
    this.foliage = windify(new THREE.MeshStandardMaterial({ vertexColors: true, map: T.leaf.map, normalMap: T.leaf.normalMap, normalScale: new THREE.Vector2(0.6, 0.6), roughness: 0.82 }), 1);
    this.stumpMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95 });
    this.berryMat = new THREE.MeshStandardMaterial({ color: 0xd8343a, roughness: 0.35, emissive: 0x70101a, emissiveIntensity: 0.6 });
    this.crystalMat = new THREE.MeshPhysicalMaterial({ color: 0xf2d27a, vertexColors: true, metalness: 0.9, roughness: 0.24, clearcoat: 0.6,
      emissive: 0xc07a10, emissiveIntensity: 0.25, envMapIntensity: 1.8, flatShading: true });
    this.rubbleMat = new THREE.MeshStandardMaterial({ map: T.rock.map, normalMap: T.rock.normalMap, roughnessMap: T.rock.roughnessMap, color: 0x8a7a5a, roughness: 1 });
    // a gold-bearing crystal cluster
    const rnd = srand(4711), parts = [];
    for (let i = 0; i < 6; i++) {
      const r = 0.02 + rnd() * 0.02, h = 0.08 + rnd() * 0.14, g = new THREE.CylinderGeometry(r * 0.4, r, h, 6, 1);
      const tip = new THREE.ConeGeometry(r * 0.35, r * 1.4, 6); tip.translate(0, h / 2 + r * 0.7, 0);
      const c = mergeGeometries([normalized(g), normalized(tip)], false);
      c.translate(0, h / 2, 0); c.rotateZ((rnd() - 0.5) * 0.7); c.rotateY(rnd() * 6.28);
      const a = i / 6 * 6.28; c.translate(Math.cos(a) * 0.06 * (i ? 1 : 0), 0, Math.sin(a) * 0.06 * (i ? 1 : 0));
      tinted(c, [0xffe08a, 0xf5c65a, 0xe6b03c, 0xfff0b8][i % 4], 0.25, 5 + i); parts.push(c);
    }
    this.crystalGeo = mergeGeometries(parts, false);
    this.rockGeo = new THREE.IcosahedronGeometry(0.5, 1); this.rockGeo.scale(1, 0.7, 0.9); jitter(this.rockGeo, 0.3, 6);
    this.berryGeo = new THREE.SphereGeometry(0.03, 7, 6);
    this.glints = [];                         // world positions where the ore catches the light
  }
  sync(terr) {
    const sig = terr.map((t) => t.cls + t.x + ',' + t.y + ':' + t.stock).join('|');
    if (sig === this.sig) return false; this.sig = sig;
    this.group.clear(); this.glints = [];
    const { wx, wz, heightAt } = this.field, dummy = new THREE.Object3D(), c = new THREE.Color();
    const bins = { pine: [], oak: [], birch: [], bush: [], stump: [], berry: [], crystal: [], rock: [] };
    for (const t of terr) {
      const h = thash(t.x, t.y), rnd = srand(t.x * 131 + t.y * 7 + 7), k = t.stock / 100, bx = wx(t.x), bz = wz(t.y);
      const put = (kind, ox, oz, s, extra) => { const x = bx + ox, z = bz + oz; bins[kind].push([x, heightAt(x, z), z, s, rnd() * 6.28, extra]); };
      if (t.cls === 'forest') {
        const n = 1 + Math.floor(h * 3);
        for (let i = 0; i < n; i++) {
          const ox = (rnd() - 0.5) * 0.66, oz = (rnd() - 0.5) * 0.66;
          if (t.stock <= 0) { put('stump', ox, oz, 0.9 + rnd() * 0.4); continue; }
          const r = rnd(), kind = r < 0.5 ? 'pine' : r < 0.82 ? 'oak' : 'birch';
          put(kind, ox, oz, (0.6 + rnd() * 0.5) * (0.42 + 0.58 * k) * 1.05, 0.88 + rnd() * 0.24);
        }
        if (t.stock > 0) for (let i = 0; i < 2; i++) put('bush', (rnd() - 0.5) * 0.8, (rnd() - 0.5) * 0.8, 0.7 + rnd() * 0.6, 1);
      } else if (t.cls === 'berries') {
        if (t.stock <= 0) { put('stump', 0, 0, 0.55); continue; }
        const n = 1 + (h > 0.5 ? 1 : 0);
        for (let i = 0; i < n; i++) {
          const ox = (rnd() - 0.5) * 0.5, oz = (rnd() - 0.5) * 0.5, s = (0.9 + rnd() * 0.4) * (0.55 + 0.45 * k);
          put('bush', ox, oz, s, 1);
          for (let d = 0; d < 6; d++) { const a = rnd() * 6.28, rr = 0.1 * s * (0.5 + rnd()); put('berry', ox + Math.cos(a) * rr, oz + Math.sin(a) * rr, 1, 0.06 + rnd() * 0.16 * s); }
        }
      } else if (t.cls === 'gold_seam') {
        if (t.stock <= 0) { put('rock', 0, 0, 0.45 + h * 0.2); put('rock', 0.2, 0.1, 0.3); continue; }
        const n = 1 + Math.floor(h * 2 + 0.6);
        for (let i = 0; i < n; i++) { const ox = (rnd() - 0.5) * 0.6, oz = (rnd() - 0.5) * 0.6, s = (0.7 + rnd() * 0.7) * (0.5 + 0.5 * k);
          put('crystal', ox, oz, s); put('rock', ox * 1.1, oz * 1.1, 0.18 + rnd() * 0.15);
          this.glints.push([bx + ox, heightAt(bx + ox, bz + oz) + 0.25 * s, bz + oz]); }
      }
    }
    const spec = { pine: [this.trees.pine, this.foliage], oak: [this.trees.oak, this.foliage], birch: [this.trees.birch, this.foliage],
      bush: [this.trees.bush, this.foliage], stump: [this.trees.stump, this.stumpMat], berry: [this.berryGeo, this.berryMat],
      crystal: [this.crystalGeo, this.crystalMat], rock: [this.rockGeo, this.rubbleMat] };
    for (const kind in bins) {
      const list = bins[kind]; if (!list.length) continue;
      const [geo, m] = spec[kind], im = new THREE.InstancedMesh(geo, m, list.length);
      list.forEach((p, i) => {
        const yoff = kind === 'berry' ? 0.1 + p[5] : kind === 'rock' ? p[3] * 0.18 : -0.015;
        dummy.position.set(p[0], p[1] + yoff, p[2]); dummy.rotation.set(0, p[4], 0);
        dummy.scale.setScalar(kind === 'berry' ? 1 : p[3]); dummy.updateMatrix(); im.setMatrixAt(i, dummy.matrix);
        if (kind === 'pine' || kind === 'oak' || kind === 'birch') im.setColorAt(i, c.setRGB(p[5], p[5], p[5]));
        if (kind === 'crystal') im.setColorAt(i, c.setRGB(1, 0.95 + thash(i, 8) * 0.1, 0.9));
      });
      im.castShadow = !(kind === 'berry'); im.receiveShadow = true; im.computeBoundingSphere(); this.group.add(im);
    }
    return true;
  }
  update(t) {
    this.crystalMat.emissiveIntensity = 0.22 + Math.sin(t * 2.1) * 0.1 + Math.sin(t * 5.7) * 0.05;
  }
}
