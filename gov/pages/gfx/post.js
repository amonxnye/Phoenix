/* Phoenix 3D — the camera's film: HDR rendering with MSAA, ambient occlusion, bloom, tone
   mapping, then a light grade (vignette, grain, chromatic fringe, split-toning). Three quality
   tiers switch the expensive parts on and off; "auto" watches the frame rate and steps. */
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';

const GradeShader = {
  uniforms: { tDiffuse: { value: null }, uTime: { value: 0 }, uVignette: { value: 0.32 }, uGrain: { value: 0.028 }, uCA: { value: 0.0005 },
    uSat: { value: 1.08 }, uContrast: { value: 1.06 }, uWarm: { value: 0.5 }, uCool: { value: 0.5 } },
  vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
  fragmentShader: `
    uniform sampler2D tDiffuse; uniform float uTime, uVignette, uGrain, uCA, uSat, uContrast, uWarm, uCool; varying vec2 vUv;
    void main() {
      vec2 c = vUv - 0.5; float r = dot(c, c); vec2 off = c * r * uCA * 8.0;
      vec3 col = vec3(texture2D(tDiffuse, vUv + off).r, texture2D(tDiffuse, vUv).g, texture2D(tDiffuse, vUv - off).b);
      float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
      col = mix(vec3(l), col, uSat); col = (col - 0.5) * uContrast + 0.5;
      col = mix(col, col * vec3(1.06, 1.0, 0.92), smoothstep(0.45, 1.0, l) * uWarm);
      col = mix(col, col * vec3(0.92, 0.98, 1.08), (1.0 - smoothstep(0.0, 0.4, l)) * uCool);
      col *= 1.0 - uVignette * smoothstep(0.22, 0.95, length(c) * 1.3);
      float n = fract(sin(dot(vUv * vec2(1731.0, 911.0) + uTime, vec2(12.9898, 78.233))) * 43758.5453);
      col += (n - 0.5) * uGrain;
      gl_FragColor = vec4(max(col, 0.0), 1.0);
    }`,
};

export const TIERS = {
  low:    { post: false, msaa: 0, ao: false, bloom: false, shadow: 1024, dpr: 1,   grass: 0.35, reflect: 0,   name: 'Low' },
  medium: { post: true,  msaa: 4, ao: false, bloom: true,  shadow: 2048, dpr: 1.25, grass: 0.7,  reflect: 512, name: 'Medium' },
  high:   { post: true,  msaa: 4, ao: true,  bloom: true,  shadow: 4096, dpr: 2,    grass: 1,    reflect: 768, name: 'High' },
};

export class Post {
  constructor(renderer, scene, camera) {
    this.renderer = renderer; this.scene = scene; this.camera = camera; this.tier = 'medium'; this.cfg = TIERS.medium;
    this.w = innerWidth; this.h = innerHeight; this.build();
  }
  build() {
    const r = this.renderer, cfg = this.cfg, pr = Math.min(devicePixelRatio || 1, cfg.dpr);
    r.setPixelRatio(pr); r.setSize(this.w, this.h);
    if (this.composer) { this.composer.dispose?.(); this.composer = null; }
    if (!cfg.post) return;
    const rt = new THREE.WebGLRenderTarget(this.w * pr, this.h * pr, { type: THREE.HalfFloatType, samples: cfg.msaa });
    const c = this.composer = new EffectComposer(r, rt); c.setPixelRatio?.(pr); c.setSize(this.w, this.h);
    c.addPass(new RenderPass(this.scene, this.camera));
    this.gtao = null;
    if (cfg.ao) {
      const ao = this.gtao = new GTAOPass(this.scene, this.camera, this.w * pr * 0.75, this.h * pr * 0.75);
      ao.output = GTAOPass.OUTPUT.Default; ao.blendIntensity = 0.85;
      ao.updateGtaoMaterial({ radius: 0.35, distanceExponent: 1.4, thickness: 1.2, scale: 1.0, samples: 12, distanceFallOff: 1, screenSpaceRadius: false });
      ao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 5, rings: 2, samples: 12 });
      // the AO depth pass must not see sprites, particles or anything flagged noAO
      ao.overrideVisibility = function () {
        const cache = this._visibilityCache;
        this.scene.traverse((o) => { cache.set(o, o.visible); if (o.isPoints || o.isLine || o.isSprite || o.userData.noAO || o.material?.isShaderMaterial || o.material?.transparent) o.visible = false; });
      };
      c.addPass(ao);
    }
    this.bloom = null;
    if (cfg.bloom) { this.bloom = new UnrealBloomPass(new THREE.Vector2(this.w, this.h), 0.35, 0.55, 1.0); c.addPass(this.bloom); }
    c.addPass(new OutputPass());
    this.grade = new ShaderPass(GradeShader); c.addPass(this.grade);
  }
  set(tier) { if (!TIERS[tier]) return; this.tier = tier; this.cfg = TIERS[tier]; this.build(); }
  resize(w, h) { this.w = w; this.h = h; this.build(); }
  /** the hour shapes the film: stronger bloom and cooler shadows at night, warm highlights at dusk */
  mood(sky) {
    if (this.bloom) { this.bloom.strength = 0.26 + 0.28 * sky.night + 0.16 * sky.dusk; this.bloom.threshold = 0.95 - 0.1 * sky.night; }
    if (this.grade) { const u = this.grade.uniforms; u.uWarm.value = 0.35 + 0.65 * sky.dusk + 0.2 * sky.day; u.uCool.value = 0.3 + 0.7 * sky.night; u.uVignette.value = 0.28 + 0.2 * sky.night; }
  }
  render(dt, t) {
    if (this.composer) { if (this.grade) this.grade.uniforms.uTime.value = t % 100; this.composer.render(dt); } else this.renderer.render(this.scene, this.camera);
  }
}
