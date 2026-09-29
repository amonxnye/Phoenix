/* Phoenix 3D — the sky: a physically-based atmosphere, a day/night cycle, stars, moon,
   drifting clouds, the lights they cast, fog, and the image-based lighting that makes every
   PBR surface pick up the colour of the sky it is under. */
import * as THREE from 'three';
import { Sky } from 'three/addons/objects/Sky.js';
import { srand, clamp, lerp, smooth, cloudTexture, glowTexture } from './util.js';

const C = (h) => new THREE.Color(h);
const FOG_DAY = C(0xb4cadb), FOG_DUSK = C(0xe9a674), FOG_NIGHT = C(0x0a1226);
const SUN_LOW = C(0xff8a3c), SUN_HIGH = C(0xfff3e2), MOON = C(0x8aa6ff);
const CLOUD_DAY = C(0xffffff), CLOUD_DUSK = C(0xffb37e), CLOUD_NIGHT = C(0x1a2340);

// the sun's daily arc: east horizon → high in the south → west horizon
const EAST = new THREE.Vector3(0.92, 0, -0.38).normalize();
const NOON = new THREE.Vector3(-0.26, 0.92, 0.30).normalize();

export const DAY_SECONDS = 720;                         // one full day, in real seconds

export function createSky(scene, renderer, { shadowHalf = 20 } = {}) {
  const root = new THREE.Group(); scene.add(root);

  /* the atmosphere */
  const sky = new Sky(); sky.scale.setScalar(4500); sky.renderOrder = -10; sky.material.depthWrite = false;
  const su = sky.material.uniforms;
  su.turbidity.value = 4; su.rayleigh.value = 1.2; su.mieCoefficient.value = 0.004; su.mieDirectionalG.value = 0.82;
  root.add(sky);

  /* deep-night dome: a faint blue gradient so the dark is never dead black */
  const night = new THREE.Mesh(new THREE.SphereGeometry(4300, 24, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, transparent: true, blending: THREE.AdditiveBlending, fog: false,
    uniforms: { k: { value: 0 } },
    vertexShader: 'varying vec3 vD; void main(){ vD = normalize(position); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
    fragmentShader: `varying vec3 vD; uniform float k;
      void main(){ float h = clamp(vD.y, -0.1, 1.0);
        vec3 zen = vec3(0.004,0.008,0.028), hor = vec3(0.028,0.045,0.10);
        gl_FragColor = vec4(mix(hor, zen, pow(h, 0.55)) * k, 1.0); }` }));
  night.renderOrder = -9; root.add(night);

  /* stars */
  const starN = 2200, sp = new Float32Array(starN * 3), sc = new Float32Array(starN * 3), rnd = srand(9001);
  for (let i = 0; i < starN; i++) {
    let x, y, z, l; do { x = rnd() * 2 - 1; y = rnd() * 2 - 1; z = rnd() * 2 - 1; l = Math.hypot(x, y, z); } while (l > 1 || l < 0.2);
    const r = 4000 / l; sp[i * 3] = x * r; sp[i * 3 + 1] = Math.abs(y) * r * 0.98 + 40; sp[i * 3 + 2] = z * r;
    const m = Math.pow(rnd(), 3), t = rnd(); const k = 0.35 + m * 0.65;
    sc[i * 3] = k * (t < 0.3 ? 1 : 0.85); sc[i * 3 + 1] = k * 0.92; sc[i * 3 + 2] = k * (t > 0.7 ? 1 : 0.9);
  }
  const sg = new THREE.BufferGeometry();
  sg.setAttribute('position', new THREE.BufferAttribute(sp, 3)); sg.setAttribute('color', new THREE.BufferAttribute(sc, 3));
  const stars = new THREE.Points(sg, new THREE.PointsMaterial({ size: 2.0, sizeAttenuation: false, vertexColors: true,
    transparent: true, opacity: 0, depthWrite: false, fog: false, blending: THREE.AdditiveBlending }));
  stars.renderOrder = -8; stars.userData.noAO = true; root.add(stars);

  /* moon and sun glow */
  const moonCv = document.createElement('canvas'); moonCv.width = moonCv.height = 128;
  { const g = moonCv.getContext('2d'), gr = g.createRadialGradient(64, 64, 18, 64, 64, 62);
    gr.addColorStop(0, 'rgba(255,255,255,0.28)'); gr.addColorStop(1, 'rgba(160,190,255,0)'); g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
    g.fillStyle = '#eef3ff'; g.beginPath(); g.arc(64, 64, 24, 0, 7); g.fill();
    g.fillStyle = 'rgba(150,165,200,0.35)'; for (const [x, y, r] of [[56, 58, 6], [72, 68, 8], [62, 76, 4], [70, 54, 4]]) { g.beginPath(); g.arc(x, y, r, 0, 7); g.fill(); } }
  const moonSpr = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(moonCv), transparent: true,
    depthWrite: false, fog: false, opacity: 0 }));
  moonSpr.scale.setScalar(520); moonSpr.renderOrder = -7; root.add(moonSpr);
  const sunGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture('rgba(255,224,170,1)', 'rgba(255,170,90,0)', 256),
    transparent: true, depthWrite: false, fog: false, blending: THREE.AdditiveBlending, opacity: 0.6 }));
  sunGlow.scale.setScalar(1500); sunGlow.renderOrder = -7; root.add(sunGlow);
  moonSpr.userData.noAO = sunGlow.userData.noAO = true;

  /* clouds: soft billboard banks that catch the light of the hour */
  const cloudTex = [cloudTexture(3), cloudTexture(11), cloudTexture(29)];
  const clouds = [], cr = srand(4242);
  for (let b = 0; b < 16; b++) {
    const cx = (cr() - 0.5) * 900, cz = -120 - cr() * 620, cy = 110 + cr() * 90, n = 4 + (cr() * 5 | 0);
    for (let j = 0; j < n; j++) {
      const m = new THREE.SpriteMaterial({ map: cloudTex[(cr() * 3) | 0], transparent: true, depthWrite: false, fog: false, opacity: 0.85 });
      const s = new THREE.Sprite(m), w = 150 + cr() * 190;
      s.scale.set(w, w * (0.34 + cr() * 0.16), 1);
      s.position.set(cx + (j - n / 2) * w * 0.55 + (cr() - 0.5) * 40, cy + (cr() - 0.5) * 12, cz + (cr() - 0.5) * 60);
      s.userData = { v: 1.4 + cr() * 1.4, noAO: true }; clouds.push(s); root.add(s);
    }
  }

  /* lights */
  const sun = new THREE.DirectionalLight(0xffffff, 3); sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -shadowHalf, right: shadowHalf, top: shadowHalf * 0.85, bottom: -shadowHalf * 0.85, near: 5, far: 200 });
  sun.shadow.bias = -0.00035; sun.shadow.normalBias = 0.03; sun.shadow.radius = 2.5;
  scene.add(sun, sun.target);
  const moon = new THREE.DirectionalLight(MOON, 0); scene.add(moon);
  const hemi = new THREE.HemisphereLight(0xbcd4ff, 0x2b2418, 0.4); scene.add(hemi);

  /* image-based lighting from the very sky we draw */
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envScene = new THREE.Scene();
  const envSky = new Sky(); envSky.scale.setScalar(4500); envScene.add(envSky);
  const envGround = new THREE.Mesh(new THREE.CircleGeometry(4000, 16), new THREE.MeshBasicMaterial({ color: 0x2c2a1c }));
  envGround.rotation.x = -Math.PI / 2; envGround.position.y = -2; envScene.add(envGround);
  let envRT = null, envAt = { el: 9, t: -99 };
  function refreshEnv(state, force) {
    const now = performance.now() / 1000;
    if (!force && Math.abs(state.el - envAt.el) < 0.012 && now - envAt.t < 8) return;
    if (!force && now - envAt.t < 1.2) return;
    const eu = envSky.material.uniforms;
    for (const k of ['turbidity', 'rayleigh', 'mieCoefficient', 'mieDirectionalG']) eu[k].value = su[k].value;
    eu.sunPosition.value.copy(state.sunDir);
    envGround.material.color.setRGB(0.10, 0.09, 0.055).multiplyScalar(0.12 + 0.88 * state.day).lerp(C(0x0a0e1c), state.night * 0.7);
    const old = envRT; envRT = pmrem.fromScene(envScene, 0, 1, 6000);
    scene.environment = envRT.texture; if (old) old.dispose();
    envAt = { el: state.el, t: now };
  }

  /* the state every other system reads */
  const state = { tod: 0.36, auto: true, sunDir: new THREE.Vector3(), moonDir: new THREE.Vector3(), el: 0,
    day: 1, night: 0, dusk: 0, exposure: 0.6, fog: new THREE.Color(), sunColor: new THREE.Color(), phase: 'day' };
  const tmp = new THREE.Vector3();

  function apply(dt, camera) {
    if (state.auto) state.tod = (state.tod + dt / DAY_SECONDS) % 1;
    const a = (state.tod - 0.25) * Math.PI * 2;
    state.sunDir.copy(EAST).multiplyScalar(Math.cos(a)).addScaledVector(NOON, Math.sin(a)).normalize();
    const s = state.sunDir.y; state.el = Math.asin(clamp(s, -1, 1));
    state.moonDir.set(-state.sunDir.x * 0.8 + 0.1, Math.max(-s, -0.2) + 0.12, -state.sunDir.z * 0.8).normalize();
    state.day = smooth(-0.04, 0.22, s);
    state.night = 1 - smooth(-0.22, 0.03, s);
    state.dusk = Math.exp(-Math.pow(s / 0.2, 2));
    state.phase = s < -0.1 ? 'night' : s < 0.06 ? (state.tod < 0.5 ? 'dawn' : 'dusk') : s < 0.4 ? 'golden' : 'day';

    su.sunPosition.value.copy(state.sunDir);
    su.turbidity.value = lerp(3.2, 9, state.dusk); su.rayleigh.value = lerp(1.1, 2.6, state.dusk) * (0.35 + 0.65 * state.day);
    su.mieCoefficient.value = lerp(0.003, 0.012, state.dusk);

    state.fog.copy(FOG_DAY).lerp(FOG_DUSK, state.dusk * 0.85 * state.day + state.dusk * 0.15).lerp(FOG_NIGHT, state.night);
    if (scene.fog) { scene.fog.color.copy(state.fog); scene.fog.density = lerp(0.0062, 0.0085, state.night) + state.dusk * 0.0012; }

    // sun: amber near the horizon, white overhead; shadows only while it is up
    state.sunColor.copy(SUN_LOW).lerp(SUN_HIGH, smooth(0.02, 0.6, s));
    sun.color.copy(state.sunColor);
    sun.intensity = 4.6 * smooth(-0.015, 0.16, s) * (0.6 + 0.4 * smooth(0.04, 0.55, s));
    sun.castShadow = sun.intensity > 0.05;
    sun.position.copy(state.sunDir).multiplyScalar(90); sun.target.position.set(0, 0, 0);
    moon.position.copy(state.moonDir).multiplyScalar(90);
    moon.intensity = 1.5 * state.night * (1 - state.day);
    hemi.color.copy(state.fog).lerp(C(0x9fb8ff), 0.35); hemi.groundColor.setRGB(0.16, 0.13, 0.08).multiplyScalar(0.5 + 0.5 * state.day);
    hemi.intensity = lerp(0.22, 0.85, state.night) * (1 - 0.4 * state.day);
    state.exposure = lerp(lerp(0.55, 0.66, state.dusk), 0.95, state.night);

    // dome, stars, moon, glow follow the camera so the horizon never moves
    root.position.copy(camera.position);
    night.material.uniforms.k.value = state.night * state.night;
    stars.material.opacity = Math.pow(state.night, 1.6);
    moonSpr.position.copy(state.moonDir).multiplyScalar(3600); moonSpr.material.opacity = clamp(state.night * 1.3) * (state.moonDir.y > -0.05 ? 1 : 0);
    sunGlow.position.copy(state.sunDir).multiplyScalar(3600);
    sunGlow.material.opacity = clamp(0.25 + 0.5 * state.dusk) * smooth(-0.1, 0.05, s);
    sunGlow.scale.setScalar(lerp(1100, 2400, state.dusk));
    const cc = tmp.set(1, 1, 1);
    for (const c of clouds) {
      c.position.x += c.userData.v * dt; if (c.position.x > 520) c.position.x = -520;
      const m = c.material; m.color.copy(CLOUD_DAY).lerp(CLOUD_DUSK, state.dusk * 0.9).lerp(CLOUD_NIGHT, state.night);
      m.color.multiplyScalar(0.28 + 0.72 * Math.max(state.day, state.dusk * 0.8)); m.opacity = lerp(0.88, 0.45, state.night);
    }
    void cc;
    refreshEnv(state, false);
    return state;
  }

  return { state, apply, sun, moon, hemi, root, forceEnv: () => refreshEnv(state, true) };
}
