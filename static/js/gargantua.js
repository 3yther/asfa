/*
 * Gargantua — raymarched gravitational lensing, shared by the /command
 * entrance overlay (blackhole.js) and the /cosmos-theme menu screen.
 *
 * Extracted rather than copied: the fragment shader is ~150 lines of physics
 * and the two callers need the identical look. Two copies would drift on the
 * first tweak.
 *
 * The march integrates a photon's path through a 1/r^2 deflection field, so
 * the disk gets sampled wherever the bent ray crosses the disk plane — which
 * is what produces the folded halo (you see the far side of the disk over and
 * under the shadow, not just the near edge). That part is unchanged from the
 * original entrance shader; what's new here is off-centre framing, the
 * Gargantua colour grade, and a real starfield.
 */

// ── Fragment shader ──────────────────────────────────────────────────────────
const VERT = "void main(){ gl_Position = vec4(position.xy, 0.0, 1.0); }";

const FRAG = [
  "precision highp float;",
  "uniform vec2 uRes; uniform float uTime; uniform float uCamDist;",
  "uniform float uPulse; uniform float uFlash;",
  "uniform vec2 uCenter;",      // screen-space offset of the hole, in uv units
  "uniform vec2 uParallax;",    // star-layer drift from pointer / device tilt
  "uniform float uRedshift;",   // 0 = neutral, 1 = fully cooled idle grade
  "uniform float uStarBright;", // 0 kills twinkle for prefers-reduced-motion
  "uniform float uNebula;",     // 0/1: faint background dust wash (optional)
  "uniform float uTurb;",       // 0/1: fine disk turbulence layer
  "uniform float uStarLayers;", // 3 or 4: number of starfield depth layers
  "",
  "float hash21(vec2 p){ p=fract(p*vec2(123.34,345.45)); p+=dot(p,p+34.345); return fract(p.x*p.y); }",
  "float hash31(vec3 p){ p=fract(p*0.1031); p+=dot(p,p.yzx+33.33); return fract((p.x+p.y)*p.z); }",
  "float vnoise(vec2 p){ vec2 i=floor(p), f=fract(p); float a=hash21(i),b=hash21(i+vec2(1.0,0.0)),",
  "  c=hash21(i+vec2(0.0,1.0)),d=hash21(i+vec2(1.0,1.0)); vec2 u=f*f*(3.0-2.0*f);",
  "  return mix(mix(a,b,u.x),mix(c,d,u.x),u.y); }",
  "float fbm(vec2 p){ float s=0.0, a=0.5; for(int i=0;i<6;i++){ s+=a*vnoise(p); p=p*2.04+1.7; a*=0.5; } return s; }",
  "float fbm3(vec2 p){ float s=0.0, a=0.5; for(int i=0;i<3;i++){ s+=a*vnoise(p); p=p*2.11+1.3; a*=0.5; } return s; }",
  "",
  // ── Accretion disk ─────────────────────────────────────────────────────────
  // Blackbody ramp + domain-warped turbulence, with relativistic beaming split
  // into BOTH brightness and colour temperature: the limb rotating toward the
  // camera reads hot amber-white, the receding limb cools and dims. The
  // original shader only varied brightness, which lost the reference's most
  // recognisable cue.
  "vec3 diskEmission(vec3 hit, float rr){",
  "  float innerR=2.0, outerR=9.0;",
  "  float tN=clamp((rr-innerR)/(outerR-innerR),0.0,1.0);",
  "  vec3 hot=vec3(1.0,0.97,0.88);",     // inner: near-white
  "  vec3 mid=vec3(1.0,0.76,0.40);",     // mid:   amber
  "  vec3 cool=vec3(0.92,0.55,0.26);",   // outer: deep amber
  "  vec3 c=mix(hot,mid,smoothstep(0.0,0.40,tN));",
  "  c=mix(c,cool,smoothstep(0.40,1.0,tN));",
  "  float ang=atan(hit.z,hit.x);",
  "  float rot=uTime*0.45*(3.0/pow(rr,1.3));",           // Keplerian: inner orbits faster
  "  float sw=ang+rot;",
  "  float warp=fbm(vec2(sw*1.5, rr*0.7));",
  "  float n1=fbm(vec2(sw*3.0+warp, rr*1.5));",
  "  float n2=fbm(vec2(sw*9.0-rot, rr*4.0+warp*2.0));",
  "  float turb=pow(mix(n1,n2,0.5),1.4);",
  "  float density=0.22+1.6*turb;",
  "  float innerEdge=smoothstep(innerR+0.5, innerR+0.04, rr);",
  "  float outerFade=smoothstep(outerR, outerR-3.0, rr);",
  "  float temp=1.0-tN;",
  "  float bright=(0.22+1.15*temp)*density*outerFade + innerEdge*0.85*temp;",
  // Fine-grained structure: filaments and knots riding on the base ramp. A
  // multiplicative +/-13% so it textures the disk without overriding the
  // blackbody gradient or the beaming grade applied below.
  // fbm3 only spans ~0.25-0.75 (octave weights 0.5/0.25/0.125), so feeding
  // it straight into the 0.87+0.26*x ramp gave +/-6.5%, not the +/-13%
  // intended, and the base turbulence swamped it (measured +0.4pp). The
  // smoothstep remaps to a true 0-1 with a hard-ish edge, which is also what
  // turns soft wobble into filaments and knots.
  // Anisotropic on purpose: high angular frequency, low radial. Keplerian
  // shear already smears the base turbulence into stripes that run along
  // the disk, so an isotropic fine layer just rides those stripes and
  // vanishes. Stretching the noise along rr makes its features cut across
  // the bands, which is what reads as filaments and knots.
  "  float fine=smoothstep(0.30, 0.62, fbm3(vec2(sw*24.0-rot*1.4, rr*2.2)));",
  "  bright*=mix(1.0, 0.86+0.28*fine, uTurb);",
  "  float beam=cos(ang);",                              // approaching/receding limb
  // Beaming runs along x, not z: for a circular orbit at (x,0,z) the velocity
  // is tangential (-z,0,x), so its line-of-sight component (camera on -z)
  // goes as x — i.e. cos(ang). The original sin(ang) put the asymmetry on the
  // top/bottom axis, where it is invisible edge-on, which is why both limbs
  // sampled identically.
  "  float dop=0.42+1.25*smoothstep(-1.0,1.0,beam);",    // brightness asymmetry
  "  vec3 warmSide=vec3(1.0,0.93,0.80);",
  "  vec3 coolSide=vec3(0.62,0.76,1.0);",                // blue-white receding limb
  "  c=mix(c*coolSide, c*warmSide, smoothstep(-0.85,0.85,beam));",
  "  return c*bright*dop;",
  "}",
  "",
  // ── Starfield ──────────────────────────────────────────────────────────────
  // One layer: a jittered star per cell of a spherical-coordinate grid, with
  // per-star size, colour temperature, twinkle phase and period all derived
  // from the cell hash so nothing pulses in sync. `shear` stretches the star
  // along the tangential axis — fed by the ray's accumulated deflection, so
  // stars smear as they pass the lensing radius rather than staying round.
  "vec3 starLayer(vec2 sph, float scale, float thresh, float sizeK, float dim, float shear){",
  "  vec2 p=sph*scale;",
  "  vec2 ip=floor(p), fp=fract(p);",
  // Clustering: low-frequency density field, so the sky has sparse and busy
  // regions instead of an even sprinkle.
  "  float dens=vnoise(ip*0.09);",
  "  float th=mix(thresh+0.030, thresh-0.045, dens);",
  "  float h=hash21(ip);",
  "  if(h<th) return vec3(0.0);",
  "  float m=(h-th)/max(1.0-th,1e-3);",                  // rank among surviving stars
  "  vec2 jit=vec2(hash21(ip+11.3), hash21(ip+37.7));",
  "  vec2 dv=fp-jit;",
  "  dv.x/=(1.0+shear*9.0);",                            // lensing streak
  "  float rad=sizeK*(0.028+0.070*m*m);",
  "  float d2=dot(dv,dv);",
  "  float core=exp(-d2/max(rad*rad,1e-6));",
  // Colour temperature, weighted toward white / blue-white with a warm tail.
  "  float ct=hash21(ip+5.77);",
  "  vec3 col=mix(vec3(0.72,0.82,1.0), vec3(1.0,1.0,1.0), smoothstep(0.0,0.55,ct));",
  "  col=mix(col, vec3(1.0,0.86,0.68), smoothstep(0.80,1.0,ct));",
  "  float tw=1.0;",
  "  if(uStarBright>0.5){",
  "    float ph=hash21(ip+91.1)*6.2831;",
  "    float sp=0.5+2.2*hash21(ip+63.4);",
  "    tw=0.72+0.28*sin(uTime*sp+ph);",
  "  }",
  // Cubic in rank: the field is dominated by faint stars with a sparse bright
  // tail, which is what a real sky looks like and what sells distance.
  "  vec3 acc=col*core*dim*tw*(0.16+1.7*m*m*m);",
  // Hero stars: the brightest few get a four-point diffraction glint.
  "  if(m>0.90){",
  "    float g=(m-0.90)/0.10;",
  "    float ax=exp(-dv.x*dv.x/(rad*rad*0.06))*exp(-dv.y*dv.y/(rad*rad*9.0));",
  "    float ay=exp(-dv.y*dv.y/(rad*rad*0.06))*exp(-dv.x*dv.x/(rad*rad*9.0));",
  "    acc+=col*(ax+ay)*g*0.55*dim*tw;",
  "  }",
  "  return acc;",
  "}",
  "",
  // Three depth layers at different angular scales. Parallax is applied at a
  // different amplitude per layer (far layers barely move), which is what sells
  // the depth on pointer movement.
  "vec3 starField(vec3 d, float shear){",
  "  vec2 sph=vec2(atan(d.z,d.x), asin(clamp(d.y,-1.0,1.0)));",
  "  vec3 col=vec3(0.004,0.005,0.010);",                  // deep-space floor
  "  if(uNebula>0.5){",
  "    float nb=fbm3(sph*1.6+vec2(3.1,7.7));",
  "    float nb2=fbm3(sph*3.3-vec2(5.2,1.9));",
  "    vec3 tint=mix(vec3(0.30,0.36,0.62), vec3(0.46,0.32,0.58), smoothstep(0.35,0.65,nb2));",
  "    tint=mix(tint, vec3(0.60,0.44,0.28), smoothstep(0.72,0.95,nb2));",
  "    col+=tint*smoothstep(0.46,0.86,nb)*0.034;",
  "  }",
  "  col+=starLayer(sph+uParallax*0.25,  34.0, 0.935, 0.90, 0.70, shear);", // near
  "  col+=starLayer(sph+uParallax*0.11,  74.0, 0.940, 0.62, 0.46, shear);", // mid
  "  col+=starLayer(sph+uParallax*0.06, 150.0, 0.935, 0.42, 0.28, shear);", // far
  "  if(uStarLayers>3.5) col+=starLayer(sph+uParallax*0.02, 260.0, 0.930, 0.30, 0.16, shear);", // deep: tiny, dim, many
  "  return col;",
  "}",
  "",
  "void main(){",
  "  vec2 uv=(gl_FragCoord.xy-0.5*uRes)/uRes.y;",
  "  uv-=uCenter;",                                       // push the hole off-centre
  "  vec3 ro=vec3(0.0,0.30,-uCamDist);",                  // near edge-on
  "  vec3 fwd=normalize(-ro);",
  "  vec3 rgt=normalize(cross(vec3(0.0,1.0,0.0),fwd));",
  "  vec3 up=cross(fwd,rgt);",
  "  vec3 dir=normalize(fwd + (uv.x*rgt + uv.y*up)*1.3);",
  "  vec3 dir0=dir;",
  "  vec3 pos=ro;",
  "  vec3 col=vec3(0.0);",
  "  float minR=1e9; bool captured=false;",
  "  const int STEPS=220; float rs=1.0; float G=1.5;",
  "  float far=max(24.0, uCamDist+14.0);",
  "  for(int i=0;i<STEPS;i++){",
  "    float r=length(pos); minR=min(minR,r);",
  "    if(r<rs){ captured=true; break; }",                 // event horizon: nothing escapes
  "    if(r>far){ break; }",
  "    float dt=clamp(r*0.08,0.02,0.40);",
  "    vec3 toC=-pos/max(r,1e-3);",
  "    dir=normalize(dir + toC*(G/(r*r))*dt);",
  "    vec3 npos=pos+dir*dt;",
  "    if(pos.y*npos.y<0.0){",                             // disk-plane crossing
  "      float h=pos.y/(pos.y-npos.y);",
  "      vec3 hit=mix(pos,npos,h); float rr=length(hit.xz);",
  "      if(rr>2.0 && rr<9.0){ col+=diskEmission(hit,rr); col=min(col,vec3(3.2)); }",
  "    }",
  "    pos=npos;",
  "  }",
  // Total angular deflection drives the star shear, so the streaking is the
  // same quantity the lensing already computed rather than a fudge.
  "  float bend=1.0-clamp(dot(dir,dir0),-1.0,1.0);",
  "  float shear=smoothstep(0.014,0.40,bend);",
  // Only stars whose rays were meaningfully bent get smeared. Feeding raw
  // `bend` in stretched every star in the sky, so the whole field read as
  // scratches rather than points with a lensed arc near the hole.
"  if(!captured){ col+=starField(dir, shear); }",
  "  float ring=pow(smoothstep(0.065,0.0,abs(minR-1.5)),1.5);",
  "  col+=vec3(1.0,0.88,0.68)*ring*1.35;",                 // photon ring: warm, not cyan
  "  col*=uPulse;",
  // Idle redshift: a few percent toward blue, reversed on any interaction.
  "  col=mix(col, col*vec3(0.88,0.95,1.12), clamp(uRedshift,0.0,1.0));",
  "  col=mix(col,vec3(1.0),clamp(uFlash,0.0,1.0));",
  "  gl_FragColor=vec4(max(col,vec3(0.0)),1.0);",          // linear HDR → OutputPass
  "}",
].join("\n");

// ── Tween helper (shared with blackhole.js's phase code) ─────────────────────
export const easeInCubic = (t) => t * t * t;
export const easeOutBack = (t) => { const c = 1.7; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };
export function tween(from, to, dur, ease, onUpdate, onDone) {
  const t0 = performance.now();
  function step(now) {
    const t = Math.min(1, (now - t0) / dur);
    onUpdate(from + (to - from) * ease(t));
    if (t < 1) requestAnimationFrame(step); else if (onDone) onDone();
  }
  requestAnimationFrame(step);
}

/*
 * Build the renderer. `opts`:
 *   container   element to append the canvas to
 *   centerX     0.5 = centred; 0.62 pushes the hole right of centre
 *   centerY     0 = centred; fraction of height, positive lifts the hole
 *   camDist     initial camera distance (11 frames the whole silhouette)
 *   reduce      prefers-reduced-motion: static frame — no disk flow, pulse,
 *               twinkle or parallax
 *   parallax    enable pointer parallax on the star layers
 *   bloom       [strength, radius, threshold]
 * Throws if the shader fails to compile, so callers can fall back.
 */
export async function createGargantua(opts) {
  const {
    container, centerX = 0.5, centerY = 0.0, camDist = 11.0, reduce = false,
    // Bloom threshold sits above the tone-mapped disk's mid-tones so only
    // genuinely hot pixels bloom. At 0.8 the entire disk qualified and the
    // halo bled across the shadow, lifting the event horizon off pure black.
    parallax = false, bloom = [0.28, 0.34, 1.05], nebula = false,
  } = opts;

  const THREE = await import("three");
  const { EffectComposer } = await import("three/addons/postprocessing/EffectComposer.js");
  const { RenderPass } = await import("three/addons/postprocessing/RenderPass.js");
  const { UnrealBloomPass } = await import("three/addons/postprocessing/UnrealBloomPass.js");
  const { OutputPass } = await import("three/addons/postprocessing/OutputPass.js");

  // This can be constructed before first layout (a hidden or backgrounded tab
  // reports 0x0), which previously produced uRes=[0,0] — the shader divides by
  // uRes.y, so every pixel came out NaN and the whole canvas rendered black.
  // Fall back to sane dimensions and re-sync on the first frames instead.
  function viewport() {
    const w = window.innerWidth || document.documentElement.clientWidth || 1280;
    const h = window.innerHeight || document.documentElement.clientHeight || 720;
    return { w, h };
  }

  const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  { const v = viewport(); renderer.setSize(v.w, v.h); }
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.85;

  let shaderFailed = false;
  renderer.debug.checkShaderErrors = true;
  renderer.debug.onShaderError = function () { shaderFailed = true; };

  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const buf = renderer.getDrawingBufferSize(new THREE.Vector2());
  if (!buf.x || !buf.y) { const v = viewport(); buf.set(v.w, v.h); }

  // centerX is a fraction of viewport width; the shader works in uv units
  // normalised by height, so convert through the aspect ratio.
  const aspect = () => { const v = viewport(); return v.w / v.h; };
  // centerY is a fraction of viewport height, positive = up. uv is already
  // normalised by height, so it maps straight through with no aspect term.
  const centerUv = () => new THREE.Vector2((centerX - 0.5) * aspect(), centerY);

  const uniforms = {
    uRes: { value: new THREE.Vector2(buf.x, buf.y) },
    uTime: { value: 0 },
    uCamDist: { value: camDist },
    uPulse: { value: 1.0 },
    uFlash: { value: 0.0 },
    uCenter: { value: centerUv() },
    uParallax: { value: new THREE.Vector2(0, 0) },
    uRedshift: { value: 0.0 },
    uStarBright: { value: reduce ? 0.0 : 1.0 },
    uNebula: { value: nebula ? 1.0 : 0.0 },
    uTurb: { value: 1.0 },
    uStarLayers: { value: 4.0 },
  };
  const mat = new THREE.ShaderMaterial({ uniforms, vertexShader: VERT, fragmentShader: FRAG });
  scene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), mat));

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const v0 = viewport();
  const bloomPass = new UnrealBloomPass(
    new THREE.Vector2(v0.w, v0.h), bloom[0], bloom[1], bloom[2]);
  composer.addPass(bloomPass);
  composer.addPass(new OutputPass());

  renderer.compile(scene, camera);
  if (shaderFailed) {
    try { renderer.dispose(); } catch (e) {}
    throw new Error("lensing shader compile failed");
  }

  container.appendChild(renderer.domElement);

  function resize() {
    const { w, h } = viewport();
    renderer.setSize(w, h); composer.setSize(w, h);
    const b = renderer.getDrawingBufferSize(new THREE.Vector2());
    uniforms.uRes.value.set(b.x || w, b.y || h);
    uniforms.uCenter.value.copy(centerUv());
  }
  window.addEventListener("resize", resize);

  // Pointer parallax. Target is eased toward each frame so the drift lags the
  // cursor slightly instead of snapping — a few milliradians of offset, which
  // at these star scales is the "few pixels" the brief asks for.
  const par = { x: 0, y: 0, tx: 0, ty: 0 };
  function onPointer(e) {
    const nx = (e.clientX / window.innerWidth) * 2 - 1;
    const ny = (e.clientY / window.innerHeight) * 2 - 1;
    par.tx = -nx * 0.035;
    par.ty = ny * 0.020;
  }
  if (parallax && !reduce) window.addEventListener("pointermove", onPointer, { passive: true });

  let running = true;
  let syncFrames = 120, lastW = 0, lastH = 0;
  const clock = new THREE.Clock();
  let onFrame = null;
  function frame() {
    if (!running) return;
    requestAnimationFrame(frame);
    const t = clock.getElapsedTime();
    // Layout may only settle after construction (hidden tab, late reveal), so
    // keep re-syncing until the real viewport matches what the shader was told.
    if (syncFrames > 0) {
      const { w, h } = viewport();
      if (w !== lastW || h !== lastH) { lastW = w; lastH = h; resize(); }
      syncFrames--;
    }
    // Under prefers-reduced-motion the clock is held, which freezes the disk
    // flow and the bloom pulse as well as the twinkle already gated by
    // uStarBright. Previously only twinkle/parallax/tweens were disabled and
    // uTime advanced regardless, so the disk kept streaming — the one piece of
    // motion most likely to bother a motion-sensitive viewer. Held at a
    // non-zero instant so the turbulence sits in a developed state rather than
    // its t=0 pattern.
    uniforms.uTime.value = reduce ? 6.0 : t;
    uniforms.uPulse.value = reduce ? 1.0 : 1.0 + 0.03 * Math.sin(t * (2.0 * Math.PI / 4.0));
    par.x += (par.tx - par.x) * 0.06;
    par.y += (par.ty - par.y) * 0.06;
    uniforms.uParallax.value.set(par.x, par.y);
    if (onFrame) onFrame(t);
    composer.render();
  }
  frame();

  const inst = {
    type: "webgl",
    renderer,
    uniforms,
    setRedshift(v) { uniforms.uRedshift.value = v; },
    setNebula(v) { uniforms.uNebula.value = v ? 1.0 : 0.0; },
    setTurb(v) { uniforms.uTurb.value = v ? 1.0 : 0.0; },
    setStarLayers(n) { uniforms.uStarLayers.value = n; },
    bloomPass,
    // Render one frame synchronously at an explicit time. Exists so the scene
    // can be pixel-verified in environments where requestAnimationFrame is
    // throttled (hidden/backgrounded tab) and the loop above never runs.
    renderOnce(t) {
      if (typeof t === "number") uniforms.uTime.value = t;
      resize();
      composer.render();
    },
    onFrame(fn) { onFrame = fn; },
    arrival() {
      if (reduce) { uniforms.uCamDist.value = camDist; return; }
      tween(uniforms.uCamDist.value, camDist, 1300, easeOutBack, (v) => { uniforms.uCamDist.value = v; });
    },
    // The fall-through used on entry and on menu selection. Unchanged timing
    // from the original entrance so both surfaces feel identical.
    fallIn(cb) {
      if (reduce) { uniforms.uFlash.value = 1.0; cb(); return; }
      tween(uniforms.uCamDist.value, 1.3, 1500, easeInCubic, (v) => { uniforms.uCamDist.value = v; }, cb);
      setTimeout(() => tween(0, 1, 260, (t) => t, (v) => { uniforms.uFlash.value = v; }), 1240);
    },
    stop() {
      running = false;
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onPointer);
      try { renderer.dispose(); renderer.forceContextLoss(); } catch (e) {}
    },
  };
  try { window.__gargantua = inst; } catch (e) {}
  return inst;
}
