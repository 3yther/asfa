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
    // A zero width means layout hasn't run yet (this module can execute before
    // first layout, and does when the tab starts hidden). That is not evidence
    // of a weak GPU — treating it as one silently forced the CSS fallback on
    // every load, so the lensing shader effectively never rendered.
    const w = window.innerWidth || document.documentElement.clientWidth || 0;
    if (w > 0 && w < 768) return true;
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

  // ── WebGL black hole ────────────────────────────────────────────────────────
  // The shader, framing and fall-in live in gargantua.js, shared with the
  // /cosmos-theme menu screen so the two can't drift. Centred and framed at
  // camDist 11 here (11.0 sits outside the disk's outer edge, so the whole
  // silhouette plus the lensed far side fits; 6.0 put the camera inside the
  // disk and blew out the framing).
  async function buildWebGL() {
    const { createGargantua } = await import("./gargantua.js");
    return createGargantua({
      container: stage,
      centerX: 0.5,
      camDist: 11.0,
      reduce,
      parallax: false,
      bloom: [0.28, 0.34, 1.05],
    });
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
