import * as THREE from 'three';

export const CONFIG = {
  camera: { fov: 52, near: 0.1, far: 500, position: [0, 1.55, 4.8], lookAt: [0, 1.40, 0] },
  colors: {
    skyZenith:  0x24383f,
    skyHorizon: 0xc3cdbe,
    skyNadir:   0x4c5c55,
    fog:        0xb4bfb2,
    sun:        0xffe6bf,
    ambientSky: 0x6d9a9c,
    ambientGnd: 0x3d4a3a,
    ground:     0x46503c,
  },
  fog: { near: 5, far: 30 },
  sun: { position: [-26, 16, -34], intensity: 2.1 },
};

const SKY_VERT = `
varying vec3 vWorldDir;
void main() {
  vWorldDir = normalize((modelMatrix * vec4(position, 1.0)).xyz);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

// The GoT look hangs on a pale mist band sitting exactly on the eyeline, with
// the sky falling off to dark teal above it. A gaussian centred on the horizon
// keeps that band tight instead of smearing it across the whole sphere.
const SKY_FRAG = `
uniform vec3 uZenith;
uniform vec3 uHorizon;
uniform vec3 uNadir;
varying vec3 vWorldDir;
void main() {
  float h = vWorldDir.y;
  vec3 col = mix(uNadir, uZenith, smoothstep(-0.35, 0.85, h));
  float band = exp(-h * h * 7.0);
  col = mix(col, uHorizon, band * 0.9);
  gl_FragColor = vec4(col, 1.0);
}`;


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
        color = mix( vec3( l ), color, 0.80 );
        color = ( color - 0.5 ) * 1.07 + 0.5;
        // Cool the shadows and leave the highlights alone — the reference sits
        // teal in the darks with the haze staying near-neutral.
        color += vec3( -0.014, 0.004, 0.020 ) * ( 1.0 - l );
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
  }

  init() {
    const c = CONFIG.colors;

    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    installGrading();
    this.renderer.toneMapping = THREE.CustomToneMapping;
    this.renderer.toneMappingExposure = 1.35;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

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

    this.hemi = new THREE.HemisphereLight(c.ambientSky, c.ambientGnd, 1.15);
    this.scene.add(this.hemi);

    // Shadow catcher and horizon filler. The grass field sits on top of this.
    this.ground = new THREE.Mesh(
      new THREE.PlaneGeometry(400, 400),
      new THREE.MeshStandardMaterial({ color: c.ground, roughness: 0.96, metalness: 0.0 })
    );
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.receiveShadow = true;
    this.scene.add(this.ground);

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
    const dpr = Math.min(window.devicePixelRatio, 2);
    // A page that boots in a background tab can measure 0 here; sizing to it
    // would put NaN in the projection matrix via aspect = w / 0.
    if (w === 0 || h === 0) return;
    const s = this._size;
    if (w === s.w && h === s.h && dpr === s.dpr) return;
    s.w = w; s.h = h; s.dpr = dpr;

    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(w, h, false);
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
      for (const m of this.modules) m.update?.(dt, t);
      this.renderer.render(this.scene, this.camera);
    };
    this._frame = requestAnimationFrame(loop);
    return this;
  }

  dispose() {
    if (this._frame) cancelAnimationFrame(this._frame);
    for (const m of this.modules) m.dispose?.();
    this._envRT?.dispose();
    this.renderer.dispose();
  }
}
