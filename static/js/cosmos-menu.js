/*
 * Particle accretion — the cosmos menu's answer to the samurai brush stroke.
 *
 * On hover/focus, light particles spawn in a ring around the item and fall
 * into the text, accelerating as they approach. The label's --heat rises in
 * step with how many have landed, so the glow and the stream peak together
 * instead of running on separate timers.
 *
 * Deliberately a plain 2D canvas on its own rAF loop with zero shared state
 * with the Three.js composer — same separation that kept the samurai slash off
 * the WebGL frame budget. One canvas per item, sized to the item's box plus a
 * spawn margin.
 */
(function () {
  "use strict";

  const menu = document.querySelector(".cosmos-menu");
  if (!menu) return;
  const items = Array.from(menu.querySelectorAll("a"));
  if (!items.length) return;

  const reduce = !!(window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  const MARGIN = 150;          // how far out particles spawn, px
  const RISE_MS = 460;         // stream duration to full brightness
  const FADE_MS = 300;         // glow fade on leave — faster than the build
  const COUNT = [15, 25];      // particles per hover event

  const rand = (a, b) => a + Math.random() * (b - a);

  // ── Per-item state ─────────────────────────────────────────────────────────
  const states = items.map((a) => {
    const canvas = document.createElement("canvas");
    canvas.className = "cosmos-particles";
    canvas.setAttribute("aria-hidden", "true");
    a.appendChild(canvas);
    return {
      a, canvas, ctx: canvas.getContext("2d"),
      parts: [], heat: 0, target: 0, active: false,
      w: 0, h: 0, dpr: Math.min(window.devicePixelRatio || 1, 2),
    };
  });

  function layout(s) {
    const r = s.a.getBoundingClientRect();
    if (!r.width || !r.height) return;
    s.w = Math.ceil(r.width + MARGIN * 2);
    s.h = Math.ceil(r.height + MARGIN * 2);
    s.canvas.style.left = -MARGIN + "px";
    s.canvas.style.top = -MARGIN + "px";
    s.canvas.style.width = s.w + "px";
    s.canvas.style.height = s.h + "px";
    s.canvas.width = Math.round(s.w * s.dpr);
    s.canvas.height = Math.round(s.h * s.dpr);
    s.textBox = { x: MARGIN, y: MARGIN, w: r.width, h: r.height };
  }
  states.forEach(layout);

  function spawn(s) {
    if (reduce) return;
    const n = Math.round(rand(COUNT[0], COUNT[1]));
    const now = performance.now();
    for (let i = 0; i < n; i++) {
      // Spawn on a ring 80–150px out, at a random bearing.
      const ang = rand(0, Math.PI * 2);
      const dist = rand(80, MARGIN);
      const cx = s.textBox.x + s.textBox.w / 2;
      const cy = s.textBox.y + s.textBox.h / 2;
      // Target a random point on the text box, not its centre — converging on
      // one point looks mechanical; scattered arrivals read as absorption.
      const tx = s.textBox.x + rand(0, s.textBox.w);
      const ty = s.textBox.y + rand(s.textBox.h * 0.15, s.textBox.h * 0.85);
      s.parts.push({
        x0: cx + Math.cos(ang) * dist,
        y0: cy + Math.sin(ang) * dist * 0.62,   // flatten: the menu is wide, not tall
        tx, ty,
        t0: now + rand(0, 120),                  // stagger arrivals
        dur: rand(RISE_MS * 0.7, RISE_MS),
        size: rand(0.8, 2.0),
        done: false,
      });
    }
  }

  // Resting → full-brightness paint, shared by the animated loop and the
  // reduced-motion path (which jumps straight to the end state).
  function paint(s) {
    const h = s.heat;
    s.a.style.color = `rgba(${Math.round(196 + 59 * h)}, ${Math.round(212 + 43 * h)}, ${Math.round(238 + 17 * h)}, ${(0.52 + 0.48 * h).toFixed(3)})`;
    s.a.style.textShadow = h < 0.01 ? "none"
      : `0 0 ${(10 * h).toFixed(1)}px rgba(190,220,255,${(0.55 * h).toFixed(3)}), ` +
        `0 0 ${(26 * h).toFixed(1)}px rgba(140,190,255,${(0.40 * h).toFixed(3)})`;
  }

  function enter(s) {
    s.active = true;
    s.target = 1;
    if (reduce) { s.heat = 1; paint(s); return; }
    layout(s);
    spawn(s);
    ensureLoop();
  }

  function leave(s) {
    s.active = false;
    s.target = 0;
    if (reduce) { s.heat = 0; paint(s); return; }
    ensureLoop();
  }

  for (const s of states) {
    s.a.addEventListener("pointerenter", () => enter(s));
    s.a.addEventListener("pointerleave", () => leave(s));
    s.a.addEventListener("focus", () => enter(s));
    s.a.addEventListener("blur", () => leave(s));
    // Touch has no hover: fire the same effect on press.
    s.a.addEventListener("pointerdown", () => enter(s));
    s.a.addEventListener("pointercancel", () => leave(s));
  }

  // ── Render loop ────────────────────────────────────────────────────────────
  // Runs only while something is hot or animating, then parks itself.
  let running = false;
  function ensureLoop() {
    if (running) return;
    running = true;
    requestAnimationFrame(frame);
  }

  function frame(now) {
    let busy = false;

    for (const s of states) {
      const ctx = s.ctx;
      if (!s.canvas.width) continue;
      ctx.setTransform(s.dpr, 0, 0, s.dpr, 0, 0);
      ctx.clearRect(0, 0, s.w, s.h);

      let landed = 0, live = 0;
      for (const p of s.parts) {
        const t = (now - p.t0) / p.dur;
        if (t < 0) { live++; continue; }
        if (t >= 1) { p.done = true; landed++; continue; }
        live++;
        // ease-in: slow drift then a rush at the end, so it reads as a pull
        // rather than a constant-velocity slide.
        const e = t * t * t;
        const x = p.x0 + (p.tx - p.x0) * e;
        const y = p.y0 + (p.ty - p.y0) * e;
        // Colour temperature tracks proximity: pale blue at spawn, white-hot
        // on arrival — same language as the disk's beaming gradient.
        const r = Math.round(150 + 105 * e);
        const g = Math.round(190 + 65 * e);
        const b = 255;
        const alpha = (t < 0.15 ? t / 0.15 : 1) * (1 - 0.15 * e);
        const rad = p.size * (1 + 0.6 * e);
        const grd = ctx.createRadialGradient(x, y, 0, x, y, rad * 3.5);
        grd.addColorStop(0, `rgba(${r},${g},${b},${alpha})`);
        grd.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = grd;
        ctx.beginPath();
        ctx.arc(x, y, rad * 3.5, 0, Math.PI * 2);
        ctx.fill();
      }

      // Heat tracks the arrival fraction directly, so it reaches full
      // brightness exactly as the last particle lands rather than easing
      // toward it afterwards. Monotonic while hot: particles land out of
      // order and the glow should never dip back down mid-stream.
      if (s.active) {
        const total = s.parts.length;
        const byArrival = total ? landed / total : 1;
        if (byArrival > s.heat) s.heat = byArrival;
      } else {
        s.heat -= (now - (s.lastT || now)) / FADE_MS;
        if (s.heat < 0) s.heat = 0;
      }
      s.lastT = now;
      if (s.parts.length && !live) s.parts.length = 0;

      // Applied directly rather than through a CSS custom property: the
      // stylesheet form (calc() over var(--heat) inside rgba()) was invalid at
      // computed-value time here, so the declaration was dropped and the
      // resting colour won — the text never lit at all.
      const h = s.heat;
      s.a.style.color = `rgba(${Math.round(196 + 59 * h)}, ${Math.round(212 + 43 * h)}, ${Math.round(238 + 17 * h)}, ${(0.52 + 0.48 * h).toFixed(3)})`;
      s.a.style.textShadow = h < 0.01 ? "none"
        : `0 0 ${(10 * h).toFixed(1)}px rgba(190,220,255,${(0.55 * h).toFixed(3)}), ` +
          `0 0 ${(26 * h).toFixed(1)}px rgba(140,190,255,${(0.40 * h).toFixed(3)})`;

      if (live || s.heat > 0.001) busy = true;
    }

    if (busy) requestAnimationFrame(frame);
    else running = false;
  }

  // ── Navigation: reuse the entrance's fall-through ──────────────────────────
  // Clicking a menu item plays the same camera fall-in the /command entrance
  // uses, then navigates. window.__cosmosFallIn is published by cosmos-boot.js
  // once the renderer exists; without it (WebGL failed, reduced motion) the
  // link just behaves normally.
  for (const a of items) {
    a.addEventListener("click", (e) => {
      const fall = window.__cosmosFallIn;
      if (!fall || reduce || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      const href = a.getAttribute("href");
      let navigated = false;
      const go = () => { if (!navigated) { navigated = true; window.location.href = href; } };
      // Navigate on the transition's callback, with a wall-clock backstop so a
      // stalled rAF (backgrounded tab) can't strand the click.
      fall(go);
      setTimeout(go, 2200);
    });
  }

  // Test affordance: drive one frame at an explicit timestamp. The loop above
  // is rAF-driven, and rAF is suspended in a hidden/backgrounded tab, so this
  // is the only way to pixel-verify the effect in automation. Same reason
  // gargantua.js exposes renderOnce().
  window.__cosmosMenuStep = (now) => frame(now);
  window.__cosmosMenuState = () => states.map((s) => ({
    text: s.a.textContent.trim(), heat: s.heat, particles: s.parts.length, active: s.active,
  }));

  let rt;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => states.forEach(layout), 150);
  });
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => states.forEach(layout));
  }
})();
