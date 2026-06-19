/* ASFA — Cinematic black-hole landing.
 *
 * Journey:  hyperspace warp (2D canvas)  →  arrival  →  idle black hole
 *           (Three.js + custom gravitational-lensing shader + bloom)  →
 *           fall-in transition  →  dashboard.
 *
 * Robustness: Three.js is dynamic-imported inside a try/catch so a CDN miss,
 * a weak GPU, or a shader-compile failure all degrade to a pure-CSS black hole
 * instead of trapping the user behind the overlay. An inline <script> safety
 * timeout (window.__bhSafety) is cleared here once we take ownership.
 */
(function () {
  "use strict";

  const root = document.getElementById("bh-landing");
  if (!root) return;

  // We're alive — cancel the HTML safety net that would force-remove the overlay.
  try { clearTimeout(window.__bhSafety); } catch (e) {}

  const stage = document.getElementById("bh-stage");
  const warpCanvas = document.getElementById("bh-warp");
  const brand = document.getElementById("bh-brand");
  const enterBtn = document.getElementById("bh-enter");
  const muteBtn = document.getElementById("bh-mute");
  const flashEl = document.getElementById("bh-flash");

  const remove = () => { try { root.remove(); } catch (e) {} };

  // ── Visit / session gating ──────────────────────────────────────────────────
  // Already entered this session → never show the black hole again.
  try {
    if (sessionStorage.getItem("asfa_bh_entered") === "1") { remove(); return; }
  } catch (e) {}

  const reduce = !!(window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  let recent = false;
  try {
    const ts = parseInt(localStorage.getItem("asfa_bh_ts") || "0", 10);
    recent = Date.now() - ts < 60 * 60 * 1000;  // visited in last hour
  } catch (e) {}
  try { localStorage.setItem("asfa_bh_ts", String(Date.now())); } catch (e) {}

  // Skip the warp on a recent visit or under reduced-motion (straight to idle).
  const skipWarp = recent || reduce;

  let phase = 0;          // 0 init · 1 warp · 2 arrival · 3 idle · 4 entering
  let entering = false;
  let bh = null;          // resolved black-hole instance (webgl | css)
  let warp = null;        // active warp controller

  // ── Tiny tween + easing helpers ─────────────────────────────────────────────
  const easeInCubic = (t) => t * t * t;
  const easeOutBack = (t) => { const c = 1.7; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };
  function tween(from, to, dur, ease, onUpdate, onDone) {
    const t0 = performance.now();
    function step(now) {
      let p = (now - t0) / dur; if (p > 1) p = 1;
      onUpdate(from + (to - from) * ease(p));
      if (p < 1) requestAnimationFrame(step);
      else if (onDone) onDone();
    }
    requestAnimationFrame(step);
  }

  // ── Optional audio: low rumble drone, muted by default ──────────────────────
  const audio = (function () {
    const AC = window.AudioContext || window.webkitAudioContext;
    let ctx = null, master = null, muted = true;
    function ensure() {
      if (ctx) return true;
      try {
        ctx = new AC();
        master = ctx.createGain(); master.gain.value = 0; master.connect(ctx.destination);
        const o1 = ctx.createOscillator(); o1.type = "sine"; o1.frequency.value = 44;
        const o2 = ctx.createOscillator(); o2.type = "sine"; o2.frequency.value = 57.5;
        const sum = ctx.createGain(); sum.gain.value = 0.5;
        o1.connect(sum); o2.connect(sum); sum.connect(master);
        o1.start(); o2.start();
        return true;
      } catch (e) { return false; }
    }
    return {
      available: () => !!AC,
      toggle() {
        if (!ensure()) return false;
        if (ctx.state === "suspended") ctx.resume();
        muted = !muted;
        const now = ctx.currentTime;
        master.gain.cancelScheduledValues(now);
        master.gain.linearRampToValueAtTime(muted ? 0 : 0.12, now + 0.6);
        return !muted;
      },
    };
  })();

  if (!audio.available()) { if (muteBtn) muteBtn.style.display = "none"; }
  else if (muteBtn) {
    muteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const on = audio.toggle();
      muteBtn.classList.toggle("bh-muted", !on);
      muteBtn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  // ── Phase 1/2 — hyperspace warp (Star-Wars style, 2D canvas) ────────────────
  function runWarp(canvas, onDone) {
    const ctx = canvas.getContext("2d");
    if (!ctx) { onDone(); return { skip() {} }; }
    let W, H, cx, cy;
    function size() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = canvas.width = Math.floor(window.innerWidth * dpr);
      H = canvas.height = Math.floor(window.innerHeight * dpr);
      canvas.style.width = window.innerWidth + "px";
      canvas.style.height = window.innerHeight + "px";
      cx = W / 2; cy = H / 2;
    }
    size();
    window.addEventListener("resize", size);

    const N = Math.min(1000, Math.floor((W * H) / 2600));
    const stars = [];
    for (let i = 0; i < N; i++) stars.push({ x: Math.random() * 2 - 1, y: Math.random() * 2 - 1, z: Math.random() });

    const WARP = 3.0, RESOLVE = 1.0;     // ~3s warp, ~1s settle into stars
    let t = 0, raf = 0, skipped = false, last = performance.now();

    // Speed envelope: accelerate → peak → dramatic deceleration → crawl.
    function envelope(tt) {
      if (tt < 2.0) { const p = tt / 2.0; return p * p; }           // accelerate
      if (tt < 3.0) { const p = (tt - 2.0) / 1.0; return 1 - 0.97 * (p * p); } // decelerate
      return 0.03;                                                  // resolve crawl
    }

    function frame(now) {
      raf = requestAnimationFrame(frame);
      let dt = (now - last) / 1000; last = now; if (dt > 0.05) dt = 0.05;
      t += dt;
      const speed = envelope(t) * 2.6;

      // Motion blur: partial clear (lighter clear at speed → longer trails).
      ctx.fillStyle = "rgba(0,2,6," + (0.55 - 0.42 * Math.min(1, speed / 2.6)) + ")";
      ctx.fillRect(0, 0, W, H);

      const shake = speed * 2.4;
      ctx.save();
      ctx.translate((Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake);
      const focal = Math.min(W, H) * 0.95;

      for (let i = 0; i < stars.length; i++) {
        const s = stars[i];
        const pz = s.z;
        s.z -= speed * dt;
        if (s.z <= 0.02) { s.x = Math.random() * 2 - 1; s.y = Math.random() * 2 - 1; s.z = 1; continue; }
        const sx = cx + (s.x / s.z) * focal;
        const sy = cy + (s.y / s.z) * focal;
        const px = cx + (s.x / pz) * focal;
        const py = cy + (s.y / pz) * focal;
        const a = Math.min(1, (1 - s.z) * 1.3);
        ctx.strokeStyle = "rgba(200,250,255," + a + ")";
        ctx.lineWidth = Math.max(0.5, (1 - s.z) * 2.6);
        ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(sx, sy); ctx.stroke();
      }
      ctx.restore();

      if (skipped || t >= WARP + RESOLVE) {
        cancelAnimationFrame(raf);
        window.removeEventListener("resize", size);
        onDone();
      }
    }
    raf = requestAnimationFrame(frame);
    return { skip() { skipped = true; } };
  }

  // ── Weak-GPU / mobile detection → CSS fallback ──────────────────────────────
  function weakGPU() {
    if (window.innerWidth < 768) return true;
    if (/Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent || "")) return true;
    try {
      const c = document.createElement("canvas");
      const gl = c.getContext("webgl2") || c.getContext("webgl");
      if (!gl) return true;
    } catch (e) { return true; }
    return false;
  }

  // ── CSS fallback black hole ─────────────────────────────────────────────────
  function buildCSS() {
    stage.innerHTML =
      '<div class="bhf">' +
      '<div class="bhf-stars"></div>' +
      '<div class="bhf-disk"></div><div class="bhf-disk bhf-disk2"></div>' +
      '<div class="bhf-jet bhf-jet-up"></div><div class="bhf-jet bhf-jet-down"></div>' +
      '<div class="bhf-core"></div><div class="bhf-ring"></div>' +
      "</div>";
    return {
      type: "css",
      arrival() { if (!reduce) stage.classList.add("bhf-arrive"); },
      fallIn(cb) {
        if (reduce) { cb(); return; }
        stage.classList.add("bhf-fall");
        setTimeout(cb, 1100);
      },
      stop() {},
    };
  }

  // ── WebGL black hole (Three.js + raymarched lensing shader + bloom) ─────────
  const VERT = "void main(){ gl_Position = vec4(position.xy, 0.0, 1.0); }";

  const FRAG = [
    "precision highp float;",
    "uniform vec2 uRes; uniform float uTime; uniform float uCamDist;",
    "uniform float uPulse; uniform float uFlash;",
    "",
    "float hash21(vec2 p){ p=fract(p*vec2(123.34,345.45)); p+=dot(p,p+34.345); return fract(p.x*p.y); }",
    "float hash31(vec3 p){ p=fract(p*0.1031); p+=dot(p,p.yzx+33.33); return fract((p.x+p.y)*p.z); }",
    "float vnoise(vec2 p){ vec2 i=floor(p), f=fract(p); float a=hash21(i),b=hash21(i+vec2(1.0,0.0)),",
    "  c=hash21(i+vec2(0.0,1.0)),d=hash21(i+vec2(1.0,1.0)); vec2 u=f*f*(3.0-2.0*f);",
    "  return mix(mix(a,b,u.x),mix(c,d,u.x),u.y); }",
    "float fbm(vec2 p){ float s=0.0, a=0.5; for(int i=0;i<5;i++){ s+=a*vnoise(p); p*=2.03; a*=0.5; } return s; }",
    "",
    "vec3 diskEmission(vec3 hit, float rr){",
    "  float innerR=2.0, outerR=8.6;",
    "  float tN=clamp((rr-innerR)/(outerR-innerR),0.0,1.0);",
    "  vec3 white=vec3(1.0,0.96,0.9), orange=vec3(1.0,0.5,0.12), deep=vec3(0.5,0.11,0.02);",
    "  vec3 c=mix(white,orange,smoothstep(0.0,0.35,tN));",
    "  c=mix(c,deep,smoothstep(0.35,1.0,tN));",
    "  float ang=atan(hit.z,hit.x);",
    "  float spin=uTime*0.55;",
    "  float swirl=ang+spin*(2.4/(rr*0.5+0.4));",         // differential rotation, inner faster
    "  float turb=fbm(vec2(swirl*1.6, rr*0.9));",
    "  float fine=fbm(vec2(swirl*5.0-spin, rr*2.6));",
    "  float density=0.5+0.85*turb+0.3*fine;",
    "  float innerGlow=smoothstep(innerR+1.5, innerR, rr);",
    "  float outerFade=smoothstep(outerR, outerR-2.6, rr);",
    "  float bright=density*outerFade*(0.45+1.7*(1.0-tN)) + innerGlow*2.3;",
    "  float dop=1.0+0.7*sin(ang);",                        // Doppler beaming asymmetry
    "  c+=vec3(0.0,0.05,0.16)*max(0.0,sin(ang));",          // slight blue shift, approaching side
    "  return c*bright*dop*0.9;",
    "}",
    "",
    "vec3 starField(vec3 d){",
    "  vec3 col=vec3(0.0); vec3 p=d*42.0; vec3 ip=floor(p); float h=hash31(ip);",
    "  if(h>0.972){ float tw=0.55+0.45*sin(uTime*2.5+h*120.0); col+=vec3(0.75,0.85,1.0)*((h-0.972)/0.028)*tw*0.9; }",
    "  col+=vec3(0.012,0.03,0.05);",                        // faint cyan haze
    "  return col;",
    "}",
    "",
    "void main(){",
    "  vec2 uv=(gl_FragCoord.xy-0.5*uRes)/uRes.y;",
    "  vec3 ro=vec3(0.0,0.42,-uCamDist);",
    "  vec3 fwd=normalize(-ro);",
    "  vec3 rgt=normalize(cross(vec3(0.0,1.0,0.0),fwd));",
    "  vec3 up=cross(fwd,rgt);",
    "  vec3 dir=normalize(fwd + (uv.x*rgt + uv.y*up)*1.4);",
    "  vec3 pos=ro;",
    "  vec3 col=vec3(0.0);",
    "  float minR=1e9; bool captured=false;",
    "  const int STEPS=160; float rs=1.0; float G=0.95;",
    "  for(int i=0;i<STEPS;i++){",
    "    float r=length(pos); minR=min(minR,r);",
    "    if(r<rs){ captured=true; break; }",
    "    if(r>22.0){ break; }",
    "    float dt=clamp(r*0.12,0.035,0.45);",
    "    vec3 toC=-pos/max(r,1e-3);",
    "    dir=normalize(dir + toC*(G/(r*r))*dt);",            // gravitational bending
    "    vec3 npos=pos+dir*dt;",
    "    if(pos.y*npos.y<0.0){",                             // accretion-disk plane crossing
    "      float h=pos.y/(pos.y-npos.y);",
    "      vec3 hit=mix(pos,npos,h); float rr=length(hit.xz);",
    "      if(rr>2.0 && rr<8.6){ col+=diskEmission(hit,rr); }",
    "    }",
    "    float axial=length(pos.xz);",                       // relativistic jets along Y
    "    if(axial<1.1 && abs(pos.y)>0.8){",
    "      float beam=exp(-axial*axial*5.0)*exp(-abs(pos.y)*0.18);",
    "      float side= pos.y>0.0 ? 1.0 : 0.82;",             // top/bottom asymmetry
    "      float flick=0.7+0.3*sin(uTime*7.0+pos.y*2.5)+0.2*vnoise(vec2(pos.y*1.5,uTime*2.0));",
    "      col+=vec3(0.25,0.85,1.0)*beam*flick*side*dt*1.6;",
    "    }",
    "    pos=npos;",
    "  }",
    "  if(!captured){ col+=starField(dir); }",
    "  float ring=smoothstep(0.5,0.0,abs(minR-1.5));",       // photon ring at ~1.5 rs
    "  float a=atan(uv.y,uv.x);",
    "  vec3 ringCol=mix(vec3(0.2,0.95,1.0),vec3(1.0,0.2,0.7),0.5+0.5*sin(a));", // chromatic split
    "  col+=ringCol*ring*1.7;",
    "  col*=uPulse;",
    "  col=vec3(1.0)-exp(-col*1.4);",                        // exposure tonemap
    "  col=pow(col,vec3(0.92));",
    "  col=mix(col,vec3(1.0),clamp(uFlash,0.0,1.0));",       // entry flash
    "  gl_FragColor=vec4(col,1.0);",
    "}",
  ].join("\n");

  async function buildWebGL() {
    const THREE = await import("three");
    const { EffectComposer } = await import("three/addons/postprocessing/EffectComposer.js");
    const { RenderPass } = await import("three/addons/postprocessing/RenderPass.js");
    const { UnrealBloomPass } = await import("three/addons/postprocessing/UnrealBloomPass.js");

    const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.setSize(window.innerWidth, window.innerHeight);

    // Trip the CSS fallback if the lensing shader fails to compile/link.
    let shaderFailed = false;
    renderer.debug.checkShaderErrors = true;
    renderer.debug.onShaderError = function () { shaderFailed = true; };

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    const buf = renderer.getDrawingBufferSize(new THREE.Vector2());
    const uniforms = {
      uRes: { value: new THREE.Vector2(buf.x, buf.y) },
      uTime: { value: 0 },
      uCamDist: { value: 12.0 },     // start far; arrival eases to 6
      uPulse: { value: 1.0 },
      uFlash: { value: 0.0 },
    };
    const mat = new THREE.ShaderMaterial({ uniforms, vertexShader: VERT, fragmentShader: FRAG });
    scene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), mat));

    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    composer.addPass(new UnrealBloomPass(
      new THREE.Vector2(window.innerWidth, window.innerHeight), 0.95, 0.7, 0.55));

    renderer.compile(scene, camera);
    if (shaderFailed) { try { renderer.dispose(); } catch (e) {} throw new Error("lensing shader compile failed"); }

    stage.appendChild(renderer.domElement);

    function resize() {
      const w = window.innerWidth, h = window.innerHeight;
      renderer.setSize(w, h); composer.setSize(w, h);
      const b = renderer.getDrawingBufferSize(new THREE.Vector2());
      uniforms.uRes.value.set(b.x, b.y);
    }
    window.addEventListener("resize", resize);

    let running = true;
    const clock = new THREE.Clock();
    function frame() {
      if (!running) return;
      requestAnimationFrame(frame);
      const t = clock.getElapsedTime();
      uniforms.uTime.value = t;
      uniforms.uPulse.value = 1.0 + 0.03 * Math.sin(t * (2.0 * Math.PI / 4.0)); // ~3% breathe / 4s
      composer.render();
    }
    frame();

    return {
      type: "webgl",
      arrival() {
        if (reduce) { uniforms.uCamDist.value = 6.0; return; }
        tween(uniforms.uCamDist.value, 6.0, 1300, easeOutBack, (v) => { uniforms.uCamDist.value = v; });
      },
      fallIn(cb) {
        if (reduce) { uniforms.uFlash.value = 1.0; cb(); return; }
        tween(uniforms.uCamDist.value, 1.3, 1500, easeInCubic, (v) => { uniforms.uCamDist.value = v; }, cb);
        setTimeout(() => tween(0, 1, 260, (t) => t, (v) => { uniforms.uFlash.value = v; }), 1240);
      },
      stop() {
        running = false;
        window.removeEventListener("resize", resize);
        try { renderer.dispose(); renderer.forceContextLoss(); } catch (e) {}
      },
    };
  }

  // ── Build the black hole ASAP, in parallel with the warp ────────────────────
  const bhReady = (async () => {
    if (weakGPU()) return buildCSS();
    try { return await buildWebGL(); }
    catch (e) { console.warn("[bh] WebGL unavailable — CSS fallback:", e); return buildCSS(); }
  })();

  // ── Phase transitions ───────────────────────────────────────────────────────
  function toIdle() {
    if (phase >= 3) return;
    phase = 3;
    if (warpCanvas) warpCanvas.classList.add("bh-warp-out");
    bhReady.then((inst) => {
      bh = inst;
      if (bh.arrival) bh.arrival();
      if (brand) brand.classList.add("bh-show");
    });
  }

  function skipToIdle() {
    if (phase >= 3) return;
    if (warp) warp.skip();
    toIdle();
  }

  function enter() {
    if (entering || phase < 3) return;
    entering = true;
    phase = 4;
    if (brand) brand.classList.remove("bh-show");

    const finish = () => {
      try { sessionStorage.setItem("asfa_bh_entered", "1"); } catch (e) {}
      document.body.classList.remove("bh-active");   // restore dashboard scroll
      root.classList.add("bh-gone");                 // fade overlay out → dashboard behind
      setTimeout(() => { if (bh && bh.stop) bh.stop(); remove(); }, 850);
    };
    const flash = () => { if (flashEl) flashEl.classList.add("bh-flash-on"); };

    if (reduce) { flash(); setTimeout(finish, 280); return; }

    bhReady.then((inst) => {
      bh = inst;
      if (bh.fallIn) bh.fallIn(() => { flash(); setTimeout(finish, 200); });
      else { flash(); setTimeout(finish, 300); }
    });
  }

  // ── Input: skip during warp · click black hole / ENTER to enter ─────────────
  document.addEventListener("keydown", (e) => {
    if (phase < 3) { skipToIdle(); }
    else if (phase === 3 && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); enter(); }
  });
  if (stage) stage.addEventListener("click", () => { if (phase === 3) enter(); });
  if (enterBtn) enterBtn.addEventListener("click", (e) => { e.stopPropagation(); enter(); });
  if (warpCanvas) warpCanvas.addEventListener("click", () => { if (phase < 3) skipToIdle(); });

  // ── Go ──────────────────────────────────────────────────────────────────────
  document.body.classList.add("bh-active");
  if (skipWarp) {
    if (warpCanvas) warpCanvas.style.display = "none";
    root.classList.add("bh-no-skiphint");
    toIdle();
  } else {
    phase = 1;
    warp = runWarp(warpCanvas, () => { phase = 2; toIdle(); });
  }
})();
