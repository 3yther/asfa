import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { Pass, FullScreenQuad } from 'three/addons/postprocessing/Pass.js';
import { CopyShader } from 'three/addons/shaders/CopyShader.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

export const CONFIG = {
  camera: { fov: 52, near: 0.1, far: 500, position: [0, 1.55, 4.8], lookAt: [0, 1.40, 0] },
  colors: {
    skyZenith:  0x38453f,
    skyHorizon: 0xd3d4c7,
    skyNadir:   0x5e645a,
    sunGlow:    0xf8ead2,
    fog:        0xc7c9bd,
    sun:        0xfff0dc,
    ambientSky: 0x9a9d90,
    ambientGnd: 0x45463c,
    ground:     0x4a4a3f,
  },
  fog: { near: 3, far: 24 },
  // Low and behind the field, opposite the camera: the whole look is rim light
  // raking toward the lens, not a key lighting the scene from the front.
  sun: { position: [-9, 4.2, -28], intensity: 2.6 },
  // Threshold sits above the fogged horizon in linear light, so only the blade
  // rim, the brightest plume tips and the sun core cross it.
  bloom: { threshold: 1.15, strength: 0.7, radius: 0.4, downscale: 4 },
  // Measured on an M-series MacBook Air at 2560×1600: the full chain ran at
  // 39 ms/frame, of which the half-resolution bloom was 19 ms. Bloom is a
  // soft effect by definition, so it runs at quarter res; and the scene is
  // fragment-bound, so DPR is capped at 1.5 rather than 2 — on a 2× display
  // that is 44% fewer pixels for a difference the mist hides. A device that
  // still measures itself slow in its first seconds steps down to 1.25.
  maxPixelRatio: 1.5,
  fallbackPixelRatio: 1.25,
  slowFrameMs: 20,
};

// Shared by the sky dome and the grass fog: the far field must dissolve into
// exactly the colour the dome shows behind it, sun bloom included.
export const SKY_GLOW_GLSL = /* glsl */`
vec3 skyGlow( vec3 dir, vec3 sunDir, vec3 glow, float band ) {
  float sd = max( dot( dir, sunDir ), 0.0 );
  // Tight core only. The camera faces the sun, so any wide falloff term
  // becomes a warm wash over every visible sky pixel instead of a low sun.
  return glow * ( pow( sd, 26.0 ) * 0.48 + pow( sd, 7.0 ) * 0.07 ) * ( 0.30 + 0.70 * band );
}`;

const SKY_VERT = `
varying vec3 vWorldDir;
void main() {
  vWorldDir = normalize((modelMatrix * vec4(position, 1.0)).xyz);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

// The GoT look hangs on a pale mist band sitting exactly on the eyeline, with
// the sky falling off to dark teal above it. A gaussian centred on the horizon
// keeps that band tight instead of smearing it across the whole sphere.
const SKY_FRAG = SKY_GLOW_GLSL + `
uniform vec3 uZenith;
uniform vec3 uHorizon;
uniform vec3 uNadir;
uniform vec3 uSunDir;
uniform vec3 uSunGlow;
varying vec3 vWorldDir;
void main() {
  float h = vWorldDir.y;
  vec3 col = mix(uNadir, uZenith, smoothstep(-0.35, 0.85, h));
  float band = exp(-h * h * 5.0);
  col = mix(col, uHorizon, band * 0.78);
  col += skyGlow(normalize(vWorldDir), uSunDir, uSunGlow, band);
  gl_FragColor = vec4(col, 1.0);
}`;


// Renders the scene into a multisampled target and resolves it once into the
// composer's plain buffer. Handing the composer itself an MSAA target makes
// every post pass write into — and resolve — a 4-sample HalfFloat buffer,
// which measured as more than the whole bloom blur chain.
class MSAAScenePass extends Pass {
  constructor(scene, camera, target) {
    super();
    this.scene = scene;
    this.camera = camera;
    this.target = target;
    this.needsSwap = true;
    this._quad = new FullScreenQuad(new THREE.ShaderMaterial({
      uniforms: THREE.UniformsUtils.clone(CopyShader.uniforms),
      vertexShader: CopyShader.vertexShader,
      fragmentShader: CopyShader.fragmentShader,
      depthTest: false, depthWrite: false,
    }));
  }
  setSize(w, h) { this.target.setSize(w, h); }
  render(renderer, writeBuffer) {
    renderer.setRenderTarget(this.target);
    renderer.clear();
    renderer.render(this.scene, this.camera);
    this._quad.material.uniforms.tDiffuse.value = this.target.texture;
    renderer.setRenderTarget(this.renderToScreen ? null : writeBuffer);
    this._quad.render(renderer);
  }
  // Frames 30–120 after boot, visible-tab only (a background tab's throttled
  // rAF would read as a slow GPU). One-way: it never steps back up.
  _adapt(dt) {
    const p = this._probe;
    if (p.done || document.visibilityState !== 'visible') return;
    p.frames++;
    if (p.frames <= 30) return;
    p.ms += dt * 1000;
    if (p.frames < 120) return;
    p.done = true;
    if (p.ms / 90 > CONFIG.slowFrameMs) {
      CONFIG.maxPixelRatio = CONFIG.fallbackPixelRatio;
      this.resize();
    }
  }

  dispose() { this.target.dispose(); this._quad.dispose(); }
}

// Colour grading folded into the tone-mapping stage rather than run as a
// post-processing pass: an EffectComposer would cost a full-screen render
// target and re-apply tone mapping on top of what the materials already did.
// THREE.ShaderChunk is global, so this must only ever be patched once.
let _gradingInstalled = false;
function installGrading() {
  if (_gradingInstalled) return;
  _gradingInstalled = true;
  THREE.ShaderChunk.tonemapping_pars_fragment =
    THREE.ShaderChunk.tonemapping_pars_fragment.replace(
      'vec3 CustomToneMapping( vec3 color ) { return color; }',
      /* glsl */`
      vec3 CustomToneMapping( vec3 color ) {
        color = ACESFilmicToneMapping( color );
        float l = dot( color, vec3( 0.2126, 0.7152, 0.0722 ) );
        color = mix( vec3( l ), color, 0.78 );
        // Highlights get contrast; shadows stay soft and lifted, never crushed.
        vec3 curved = color * color * ( 3.0 - 2.0 * color );
        color = mix( color, curved, smoothstep( 0.35, 0.9, l ) * 0.5 );
        color = color * 0.965 + 0.026;
        // Warm overall, with the darks pulled toward grey-green rather than teal.
        color *= vec3( 1.0, 1.0, 0.985 );
        color += vec3( -0.008, 0.005, 0.0 ) * ( 1.0 - l );
        return clamp( color, 0.0, 1.0 );
      }`
    );
}

export class SamuraiScene {
  constructor(canvas) {
    this.canvas = canvas;
    this.modules = [];
    this.clock = new THREE.Clock();
    this._frame = null;
    this._size = { w: 0, h: 0, dpr: 0 };
    this._probe = { frames: 0, ms: 0, done: false };
  }

  init() {
    const c = CONFIG.colors;

    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, CONFIG.maxPixelRatio));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    installGrading();
    this.renderer.toneMapping = THREE.CustomToneMapping;
    this.renderer.toneMappingExposure = 1.35;
    this.renderer.shadowMap.enabled = true;
    // PCF rather than PCFSoft: the soft kernel is sampled per fragment across
    // 42k blades and the shadow on the field is a diffuse streak either way.
    this.renderer.shadowMap.type = THREE.PCFShadowMap;

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(c.fog, CONFIG.fog.near, CONFIG.fog.far);

    this.camera = new THREE.PerspectiveCamera(CONFIG.camera.fov, 1, CONFIG.camera.near, CONFIG.camera.far);
    this.camera.position.set(...CONFIG.camera.position);
    this.camera.lookAt(new THREE.Vector3(...CONFIG.camera.lookAt));

    this.sky = new THREE.Mesh(
      new THREE.SphereGeometry(400, 32, 16),
      new THREE.ShaderMaterial({
        uniforms: {
          uZenith:  { value: new THREE.Color(c.skyZenith) },
          uHorizon: { value: new THREE.Color(c.skyHorizon) },
          uNadir:   { value: new THREE.Color(c.skyNadir) },
          uSunDir:  { value: new THREE.Vector3(...CONFIG.sun.position).normalize() },
          uSunGlow: { value: new THREE.Color(c.sunGlow) },
        },
        vertexShader: SKY_VERT,
        fragmentShader: SKY_FRAG,
        side: THREE.BackSide,
        depthWrite: false,
        fog: false,
      })
    );
    this.scene.add(this.sky);

    // A fully metallic material has no diffuse term — with nothing to reflect
    // the blade renders black. Prefilter the sky itself into an environment map
    // so the steel picks up the teal haze instead of a studio probe.
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    pmrem.compileEquirectangularShader();
    // far must clear the sky sphere's radius — fromScene defaults to 100, which
    // clips a 400-unit dome entirely and bakes a black environment.
    this._envRT = pmrem.fromScene(this.scene, 0, 1, 1000);
    this.scene.environment = this._envRT.texture;
    pmrem.dispose();

    this.sun = new THREE.DirectionalLight(c.sun, CONFIG.sun.intensity);
    this.sun.position.set(...CONFIG.sun.position);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.camera.near = 1;
    this.sun.shadow.camera.far = 120;
    this.sun.shadow.camera.left = -30;
    this.sun.shadow.camera.right = 30;
    this.sun.shadow.camera.top = 30;
    this.sun.shadow.camera.bottom = -30;
    this.sun.shadow.bias = -0.0008;
    this.scene.add(this.sun);

    this.hemi = new THREE.HemisphereLight(c.ambientSky, c.ambientGnd, 0.8);
    this.scene.add(this.hemi);

    // Shadow catcher and horizon filler. The grass field sits on top of this.
    const groundMat = new THREE.MeshStandardMaterial({ color: c.ground, roughness: 0.96, metalness: 0.0 });
    // Plain fog would lift the ground to a flat grey while the dome behind it
    // carries the sun bloom — a visible seam right on the horizon. Fog the
    // ground toward the same colour the dome shows in that direction.
    groundMat.onBeforeCompile = (sh) => {
      sh.uniforms.uSunDir = { value: new THREE.Vector3(...CONFIG.sun.position).normalize() };
      sh.uniforms.uSunGlow = { value: new THREE.Color(c.sunGlow) };
      sh.vertexShader = sh.vertexShader
        .replace('#include <common>', '#include <common>\n varying vec3 vGWorld;')
        .replace('#include <begin_vertex>', '#include <begin_vertex>\n vGWorld = ( modelMatrix * vec4( position, 1.0 ) ).xyz;');
      sh.fragmentShader = sh.fragmentShader
        .replace('#include <common>', '#include <common>\n uniform vec3 uSunDir; uniform vec3 uSunGlow; varying vec3 vGWorld;' + SKY_GLOW_GLSL)
        .replace('#include <fog_fragment>', `
          #ifdef USE_FOG
            vec3 fdir = normalize( vGWorld - cameraPosition );
            vec3 fcol = fogColor + skyGlow( fdir, uSunDir, uSunGlow, exp( -fdir.y * fdir.y * 5.0 ) );
            gl_FragColor.rgb = mix( gl_FragColor.rgb, fcol, smoothstep( fogNear, fogFar, vFogDepth ) );
          #endif`);
    };
    this.ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), groundMat);
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.receiveShadow = true;
    this.scene.add(this.ground);

    // Post chain. The scene pass renders into a multisampled HalfFloat target
    // so MSAA — and with it the alpha-to-coverage feathering on the plumes —
    // survives the bloom; everything after it runs single-sample. OutputPass
    // owns tone mapping (and the grading patched into it); the materials skip
    // it when drawing off-screen.
    const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    // 2 samples, not 4: with every scene object hidden the 4-sample 16-bit
    // target alone measured 12 ms/frame at 1920×1200 on an M-series GPU —
    // more than all the content. Two samples halve that for a small loss in
    // thin-blade smoothing that the mist mostly hides.
    const msaa = new THREE.WebGLRenderTarget(size.x, size.y, {
      type: THREE.HalfFloatType, samples: 2, depthBuffer: true,
    });
    this.composer = new EffectComposer(this.renderer,
      new THREE.WebGLRenderTarget(size.x, size.y, { type: THREE.HalfFloatType }));
    this.composer.addPass(new MSAAScenePass(this.scene, this.camera, msaa));
    const b = CONFIG.bloom;
    this.bloom = new UnrealBloomPass(new THREE.Vector2(size.x / b.downscale, size.y / b.downscale), b.strength, b.radius, b.threshold);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    this.resize();
    return this;
  }

  add(module) {
    this.modules.push(module);
    return this;
  }

  // Called every frame instead of from a resize event: devicePixelRatio changes
  // (dragging the window to another display) fire no event at all, and a
  // non-rendering page suspends ResizeObserver callbacks just as it does rAF —
  // so a frame-time check is both sufficient and the only thing that covers DPR.
  // Bails immediately unless something actually changed.
  resize() {
    const w = this.canvas.clientWidth || window.innerWidth;
    const h = this.canvas.clientHeight || window.innerHeight;
    const dpr = Math.min(window.devicePixelRatio, CONFIG.maxPixelRatio);
    // A page that boots in a background tab can measure 0 here; sizing to it
    // would put NaN in the projection matrix via aspect = w / 0.
    if (w === 0 || h === 0) return;
    const s = this._size;
    if (w === s.w && h === s.h && dpr === s.dpr) return;
    s.w = w; s.h = h; s.dpr = dpr;

    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(w, h, false);
    // The composer caches the pixel ratio it was built with; keep it honest.
    this.composer?.setPixelRatio(dpr);
    this.composer?.setSize(w, h);
    this.bloom?.setSize(w * dpr / CONFIG.bloom.downscale, h * dpr / CONFIG.bloom.downscale);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    for (const m of this.modules) m.resize?.(w, h);
  }

  start() {
    const loop = () => {
      this._frame = requestAnimationFrame(loop);
      this.resize();
      const dt = Math.min(this.clock.getDelta(), 0.1);
      const t = this.clock.elapsedTime;
      this._adapt(dt);
      for (const m of this.modules) m.update?.(dt, t);
      this.composer.render();
    };
    this._frame = requestAnimationFrame(loop);
    return this;
  }

  // Frames 30–120 after boot, visible-tab only (a background tab's throttled
  // rAF would read as a slow GPU). One-way: it never steps back up.
  _adapt(dt) {
    const p = this._probe;
    if (p.done || document.visibilityState !== 'visible') return;
    p.frames++;
    if (p.frames <= 30) return;
    p.ms += dt * 1000;
    if (p.frames < 120) return;
    p.done = true;
    if (p.ms / 90 > CONFIG.slowFrameMs) {
      CONFIG.maxPixelRatio = CONFIG.fallbackPixelRatio;
      this.resize();
    }
  }

  dispose() {
    if (this._frame) cancelAnimationFrame(this._frame);
    for (const m of this.modules) m.dispose?.();
    this._envRT?.dispose();
    this.composer?.dispose();
    this.renderer.dispose();
  }
}
