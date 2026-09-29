/* Phoenix 3D — shared helpers: seeded randomness, noise, and procedural PBR textures.
   Everything here is deterministic (the same world draws identically on every load) and
   offline: every texture is painted on a canvas at start-up, nothing is fetched. */
import * as THREE from 'three';

/* ── seeded randomness (arnis pattern — entity-seeded, stable across runs) ───────── */
export const srand = seed => {
  let a = (seed * 2654435761) >>> 0;
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};
export const thash = (x, y) => {
  let h = ((x * 374761393 + y * 668265263) ^ (x * y * 2246822519 + 1)) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
};
export const strSeed = s => [...String(s)].reduce((a, c) => (a * 33 + c.charCodeAt(0)) >>> 0, 7) || 1;

export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a, b, t) => a + (b - a) * t;
export const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };

/* ── value noise (non-tileable, for terrain) ─────────────────────────────────────── */
function h2(ix, iy, seed) {
  let h = (Math.imul(ix, 374761393) + Math.imul(iy, 668265263) + Math.imul(seed, 2246822519)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
export function vnoise(x, y, seed = 0) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  const a = h2(ix, iy, seed), b = h2(ix + 1, iy, seed), c = h2(ix, iy + 1, seed), d = h2(ix + 1, iy + 1, seed);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
export function fbm(x, y, oct = 4, seed = 0, lac = 2, gain = 0.5) {
  let s = 0, a = 0.5, f = 1, n = 0;
  for (let i = 0; i < oct; i++) { s += a * vnoise(x * f, y * f, seed + i * 17); n += a; a *= gain; f *= lac; }
  return s / n;
}
export function ridged(x, y, oct = 4, seed = 0) {
  let s = 0, a = 0.5, f = 1, n = 0;
  for (let i = 0; i < oct; i++) {
    const v = 1 - Math.abs(vnoise(x * f, y * f, seed + i * 31) * 2 - 1);
    s += a * v * v; n += a; a *= 0.5; f *= 2;
  }
  return s / n;
}

/* ── tileable noise (for textures) ───────────────────────────────────────────────── */
function pn(x, y, per, seed) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  const m = (i) => ((i % per) + per) % per;
  const a = h2(m(ix), m(iy), seed), b = h2(m(ix + 1), m(iy), seed);
  const c = h2(m(ix), m(iy + 1), seed), d = h2(m(ix + 1), m(iy + 1), seed);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
/** tileable fractal noise: `per` lattice cells across the unit square at octave 0 */
export function tfbm(u, v, per, oct = 4, seed = 0) {
  let s = 0, a = 0.5, n = 0, p = per;
  for (let i = 0; i < oct; i++) { s += a * pn(u * p, v * p, p, seed + i * 13); n += a; a *= 0.5; p *= 2; }
  return s / n;
}
/** tileable cellular noise → [f1, f2, cellId] */
export function tcell(u, v, per, seed = 0) {
  const x = u * per, y = v * per, ix = Math.floor(x), iy = Math.floor(y);
  let f1 = 9, f2 = 9, id = 0;
  for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) {
    const cx = ix + i, cy = iy + j;
    const wx = ((cx % per) + per) % per, wy = ((cy % per) + per) % per;
    const px = cx + h2(wx, wy, seed) * 0.85 + 0.075, py = cy + h2(wx, wy, seed + 71) * 0.85 + 0.075;
    const d = Math.hypot(px - x, py - y);
    if (d < f1) { f2 = f1; f1 = d; id = h2(wx, wy, seed + 9); } else if (d < f2) f2 = d;
  }
  return [f1, f2, id];
}

/* ── procedural PBR texture sets ─────────────────────────────────────────────────── */
/** paint(size, fn) — fn(u, v, out) fills out = [r, g, b (0-1 albedo), height, roughness].
    Returns {map, normalMap, roughnessMap} as tiling canvas textures. */
export function paint(size, fn, { normalStrength = 3, tint = null } = {}) {
  const N = size, col = new Uint8ClampedArray(N * N * 4), rou = new Uint8ClampedArray(N * N * 4);
  const hgt = new Float32Array(N * N), out = [0, 0, 0, 0, 0];
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    fn(x / N, y / N, out);
    const i = (y * N + x) * 4;
    col[i] = clamp(out[0]) * 255; col[i + 1] = clamp(out[1]) * 255; col[i + 2] = clamp(out[2]) * 255; col[i + 3] = 255;
    hgt[y * N + x] = out[3];
    rou[i] = rou[i + 2] = 255; rou[i + 1] = clamp(out[4]) * 255; rou[i + 3] = 255;   // green = roughness
  }
  const nor = new Uint8ClampedArray(N * N * 4), at = (x, y) => hgt[((y + N) % N) * N + ((x + N) % N)];
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const dx = (at(x + 1, y) - at(x - 1, y)) * normalStrength * N * 0.05;
    const dy = (at(x, y + 1) - at(x, y - 1)) * normalStrength * N * 0.05;
    const l = Math.hypot(dx, dy, 1), i = (y * N + x) * 4;
    nor[i] = (-dx / l * 0.5 + 0.5) * 255; nor[i + 1] = (dy / l * 0.5 + 0.5) * 255; nor[i + 2] = (1 / l * 0.5 + 0.5) * 255; nor[i + 3] = 255;
  }
  const mk = (data, srgb) => {
    const c = document.createElement('canvas'); c.width = c.height = N;
    c.getContext('2d').putImageData(new ImageData(data, N, N), 0, 0);
    const t = new THREE.CanvasTexture(c);
    t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 8;
    if (srgb) t.colorSpace = THREE.SRGBColorSpace;
    return t;
  };
  return { map: mk(col, true), normalMap: mk(nor, false), roughnessMap: mk(rou, false), size: N };
}

const mix3 = (o, a, b, t) => { o[0] = a[0] + (b[0] - a[0]) * t; o[1] = a[1] + (b[1] - a[1]) * t; o[2] = a[2] + (b[2] - a[2]) * t; };

/** every texture set the world uses — built once, shared by all materials */
export function makeTextures() {
  const T = {};
  T.ground = paint(512, (u, v, o) => {
    const a = tfbm(u, v, 8, 5, 1), b = tfbm(u, v, 32, 3, 5), s = pn(u * 256, v * 256, 256, 9);
    const k = 0.9 + (a - 0.5) * 0.16 + (b - 0.5) * 0.08 + (s > 0.93 ? -0.07 : 0);
    o[0] = o[1] = o[2] = k; o[3] = a * 0.7 + b * 0.5 + s * 0.15; o[4] = 0.93;
  }, { normalStrength: 0.9 });

  T.rock = paint(256, (u, v, o) => {
    const [f1, f2] = tcell(u, v, 5, 3), crack = smooth(0.0, 0.11, f2 - f1);
    const strata = tfbm(u, v * 0.5 + 0.2, 6, 4, 8), gr = tfbm(u, v, 48, 2, 4);
    const k = (0.5 + strata * 0.4) * (0.7 + 0.3 * crack) + (gr - 0.5) * 0.14;
    o[0] = k * 1.02; o[1] = k * 0.97; o[2] = k * 0.9;
    o[3] = crack * 0.6 + strata * 0.5 + gr * 0.12; o[4] = 0.88;
  }, { normalStrength: 4 });

  T.stone = paint(256, (u, v, o) => {              // ashlar blocks with mortar joints
    const rows = 8, ry = v * rows, row = Math.floor(ry), fy = ry - row;
    const cols = 4 + (row % 3), off = (row % 2) * 0.5 / cols, cx = (u + off) * cols, col = Math.floor(cx), fx = cx - col;
    const id = h2(col, row, 21), edge = Math.min(fx, 1 - fx) * cols / rows, ey = Math.min(fy, 1 - fy);
    const joint = smooth(0.0, 0.05, Math.min(edge, ey));
    const g = tfbm(u, v, 24, 3, 4);
    const k = (0.8 + id * 0.14) * (0.92 + g * 0.16) * (0.74 + 0.26 * joint);
    o[0] = k * 1.03; o[1] = k; o[2] = k * 0.95;
    o[3] = joint * 0.7 + g * 0.2 + id * 0.08; o[4] = 0.86 + (1 - joint) * 0.1;
  }, { normalStrength: 2.6 });

  T.plaster = paint(256, (u, v, o) => {
    const a = tfbm(u, v, 5, 4, 2), b = tfbm(u * 1.0, v * 0.25, 14, 3, 6), sp = pn(u * 128, v * 128, 128, 4);
    const k = 0.84 + a * 0.22 + (b - 0.5) * 0.12 - (sp > 0.95 ? 0.08 : 0);
    o[0] = k; o[1] = k * 0.985; o[2] = k * 0.95; o[3] = a * 0.5 + b * 0.3 + sp * 0.1; o[4] = 0.92;
  }, { normalStrength: 1.6 });

  T.planks = paint(256, (u, v, o) => {             // vertical boards with grain and gaps
    const n = 8, cx = u * n, id = Math.floor(cx), fx = cx - id;
    const gap = smooth(0.0, 0.05, Math.min(fx, 1 - fx));
    const grain = tfbm(u * 0.6 + id * 0.37, v * 0.06, 24, 3, 12 + id), rings = Math.sin((grain * 9 + fx * 2) * 6.28) * 0.5 + 0.5;
    const k = (0.5 + h2(id, 0, 3) * 0.32) * (0.75 + grain * 0.35) * (0.86 + rings * 0.14) * (0.45 + 0.55 * gap);
    o[0] = k * 1.06; o[1] = k * 0.92; o[2] = k * 0.74;
    o[3] = gap * 0.6 + grain * 0.3 + rings * 0.1; o[4] = 0.82;
  }, { normalStrength: 2.6 });

  T.tiles = paint(256, (u, v, o) => {              // overlapping scalloped roof tiles
    const rows = 10, ry = v * rows, row = Math.floor(ry), ty = ry - row;
    const cols = 8, cx = (u * cols + (row % 2) * 0.5), col = Math.floor(cx), tx = cx - col;
    const round = 1 - Math.pow(Math.abs(tx - 0.5) * 2, 3) * 0.16;
    const lip = smooth(0.0, 0.16, ty) * (1 - smooth(0.82, 1.0, ty) * 0.1);
    const id = h2(col, row, 55), g = tfbm(u, v, 20, 2, 1);
    const k = (0.62 + id * 0.3) * (0.42 + 0.58 * lip) * round * (0.88 + g * 0.24);
    o[0] = k; o[1] = k * 0.98; o[2] = k * 0.96;
    o[3] = lip * 0.7 + (ty > 0.7 ? 0.25 : 0) + id * 0.06; o[4] = 0.7 + (1 - lip) * 0.2;
  }, { normalStrength: 3.6 });

  T.thatch = paint(256, (u, v, o) => {
    const a = tfbm(u * 2.2, v * 0.16, 28, 3, 7), b = tfbm(u * 5.0, v * 0.5, 56, 2, 3);
    const band = Math.sin(v * 6.28 * 5 + a * 3) * 0.5 + 0.5, k = (0.55 + a * 0.4) * (0.78 + band * 0.22);
    o[0] = k * 1.08; o[1] = k * 0.9; o[2] = k * 0.5; o[3] = a * 0.6 + b * 0.4 + band * 0.2; o[4] = 0.95;
  }, { normalStrength: 3.2 });

  T.cobble = paint(256, (u, v, o) => {
    const [f1, f2, id] = tcell(u, v, 7, 17), dome = smooth(0.0, 0.5, f2 - f1) , edge = smooth(0.02, 0.09, f2 - f1);
    const g = tfbm(u, v, 40, 2, 6);
    const k = (0.5 + id * 0.36) * (0.7 + 0.3 * dome) * (0.4 + 0.6 * edge) * (0.85 + g * 0.3);
    o[0] = k * 1.02; o[1] = k * 0.98; o[2] = k * 0.9; o[3] = dome * 0.6 + edge * 0.5; o[4] = 0.88;
  }, { normalStrength: 4 });

  T.leaf = paint(128, (u, v, o) => {               // dense foliage / hedge mottling
    const a = tfbm(u, v, 10, 4, 2), [f1] = tcell(u, v, 10, 8);
    const k = 0.6 + a * 0.45 - f1 * 0.25; o[0] = k * 0.95; o[1] = k * 1.05; o[2] = k * 0.7;
    o[3] = a * 0.6 + (1 - f1) * 0.4; o[4] = 0.85;
  }, { normalStrength: 2.4 });

  // water ripple normals for the reflective surface (Water.js samples it as a colour texture)
  T.water = paint(256, (u, v, o) => {
    const h = tfbm(u, v, 4, 5, 33) + tfbm(u, v, 16, 3, 44) * 0.35;
    o[0] = o[1] = o[2] = 0.5; o[3] = h; o[4] = 0.05;
  }, { normalStrength: 5 }).normalMap;
  T.water.colorSpace = THREE.NoColorSpace;
  return T;
}

/** soft cloud puff for sprites: fractal alpha inside a radial falloff */
export function cloudTexture(seed = 3) {
  const N = 256, c = document.createElement('canvas'); c.width = c.height = N;
  const g = c.getContext('2d'), img = g.createImageData(N, N);
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const u = x / N, v = y / N, dx = (u - 0.5) * 2, dy = (v - 0.5) * 2.4;
    const r = Math.hypot(dx, dy), f = fbm(u * 5, v * 5, 5, seed);
    const a = clamp((1 - r) * 1.5) * clamp(f * 1.7 - 0.28);
    const shade = 0.72 + 0.28 * (1 - v);           // brighter top, greyer belly
    const i = (y * N + x) * 4;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = 255 * shade; img.data[i + 3] = 255 * a * a;
  }
  g.putImageData(img, 0, 0);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

/** soft round glow, for lamps, sun halos, sparkles */
export function glowTexture(inner = 'rgba(255,255,255,1)', outer = 'rgba(255,255,255,0)', size = 128) {
  const c = document.createElement('canvas'); c.width = c.height = size;
  const g = c.getContext('2d'), gr = g.createRadialGradient(size / 2, size / 2, 1, size / 2, size / 2, size / 2 - 1);
  gr.addColorStop(0, inner); gr.addColorStop(0.35, inner.replace(/[\d.]+\)$/, '0.45)')); gr.addColorStop(1, outer);
  g.fillStyle = gr; g.fillRect(0, 0, size, size);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

/* ── geometry helpers ────────────────────────────────────────────────────────────── */
/** add per-vertex colour to a geometry (constant colour with optional noisy variation) */
export function tinted(geo, hex, vary = 0, seed = 1) {
  const c = new THREE.Color(hex), p = geo.attributes.position, col = new Float32Array(p.count * 3), rnd = srand(seed);
  for (let i = 0; i < p.count; i++) {
    const k = 1 + (rnd() - 0.5) * vary;
    col[i * 3] = c.r * k; col[i * 3 + 1] = c.g * k; col[i * 3 + 2] = c.b * k;
  }
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  return geo;
}
/** colour a geometry by height: bottom → top gradient (great for foliage and grass) */
export function gradientColor(geo, hexBottom, hexTop, y0, y1, noise = 0, seed = 1) {
  const a = new THREE.Color(hexBottom), b = new THREE.Color(hexTop), p = geo.attributes.position;
  const col = new Float32Array(p.count * 3), rnd = srand(seed), c = new THREE.Color();
  for (let i = 0; i < p.count; i++) {
    const t = clamp((p.getY(i) - y0) / (y1 - y0));
    c.copy(a).lerp(b, t).multiplyScalar(1 + (rnd() - 0.5) * noise);
    col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
  }
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  return geo;
}
/** jitter vertices for an organic silhouette (deterministic by position) */
export function jitter(geo, amount, seed = 1) {
  const p = geo.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    const k = (vnoise(x * 3.1 + seed, z * 3.1 + y * 2.3, seed) - 0.5) * amount;
    p.setXYZ(i, x + k * 0.8, y + k * 0.6, z + k);
  }
  geo.computeVertexNormals();
  return geo;
}
/** weld a geometry down to the attribute set every merged piece shares */
export function normalized(geo) {
  const g = geo.index ? geo.toNonIndexed() : geo;
  for (const k of Object.keys(g.attributes)) if (!['position', 'normal', 'uv', 'color'].includes(k)) g.deleteAttribute(k);
  if (!g.attributes.uv) g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(g.attributes.position.count * 2), 2));
  if (!g.attributes.color) {
    const n = g.attributes.position.count, col = new Float32Array(n * 3).fill(1);
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  }
  return g;
}
