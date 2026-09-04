// Login-entrance slash transition. Fires only when the samurai page was
// reached via a login redirect (login() appends ?entry=1 — see app.py),
// never on a plain navigation to /samurai-theme (nav link, browser back,
// bookmark). A 2D canvas overlay, entirely outside the Three.js render loop
// in samurai-boot.js, so it costs that scene's frame budget nothing by
// construction — there's no shared canvas, no shared rAF loop, no GL calls.
(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const shouldPlay = params.get("entry") === "1";
  if (!shouldPlay) return;

  // Strip the flag immediately so a manual reload (or sharing the URL)
  // doesn't replay the slash — it only ever fires once, on the redirect
  // that put it there.
  params.delete("entry");
  const clean = location.pathname + (params.toString() ? "?" + params.toString() : "") + location.hash;
  history.replaceState(null, "", clean);

  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return; // Menu is already visible underneath; nothing to skip past.
  }

  playSlashAnimation();

  function playWhoosh() {
    try {
      if (localStorage.getItem("asfa_sounds") !== "true") return;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      const ac = new AC();
      if (ac.state === "suspended") ac.resume();

      // Filtered noise burst with a falling bandpass sweep — reads as a
      // fast blade "whoosh" without needing an audio asset.
      const dur = 0.26;
      const bufSize = Math.max(1, Math.round(ac.sampleRate * dur));
      const buf = ac.createBuffer(1, bufSize, ac.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < bufSize; i++) data[i] = Math.random() * 2 - 1;

      const src = ac.createBufferSource();
      src.buffer = buf;

      const filter = ac.createBiquadFilter();
      filter.type = "bandpass";
      filter.Q.value = 1.1;
      filter.frequency.setValueAtTime(3400, ac.currentTime);
      filter.frequency.exponentialRampToValueAtTime(500, ac.currentTime + dur);

      const gain = ac.createGain();
      gain.gain.setValueAtTime(0.0001, ac.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.16, ac.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + dur);

      src.connect(filter);
      filter.connect(gain);
      gain.connect(ac.destination);
      src.start();
      src.stop(ac.currentTime + dur + 0.02);
      src.onended = () => ac.close().catch(() => {});
    } catch (e) { /* audio unavailable — the slash still plays silently */ }
  }

  function playSlashAnimation(onComplete) {
    const canvas = document.createElement("canvas");
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.style.position = "fixed";
    canvas.style.inset = "0";
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.style.pointerEvents = "none";
    canvas.style.zIndex = "9999";
    canvas.style.opacity = "1";
    document.body.appendChild(canvas);
    const ctx = canvas.getContext("2d");

    function fit() {
      canvas.width = Math.round(window.innerWidth * dpr);
      canvas.height = Math.round(window.innerHeight * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    fit();
    window.addEventListener("resize", fit);

    // Two diagonal strokes in quick succession, staggered ~60ms apart and
    // offset vertically, each ~150ms top-left → bottom-right.
    const TRAVEL = 150;
    const STAGGER = 60;
    const strokes = [
      { start: 0, yBias: -0.10 },
      { start: STAGGER, yBias: 0.12 },
    ];
    const actionEnd = Math.max(...strokes.map(s => s.start)) + TRAVEL;
    const FADE = 100;

    const W = window.innerWidth, H = window.innerHeight;
    const MARGIN = 140;   // enter/exit off-screen so the tip is never clipped mid-stroke
    const WIDTH = 48;     // slash thickness, px
    const TRAILS = 9;     // motion-blur copies per frame

    function drawStroke(t01, yBias) {
      // t01: 0 → entering off the top-left, 1 → exited off the bottom-right.
      const x0 = -MARGIN, y0 = H * (0.08 + yBias) - MARGIN * 0.3;
      const x1 = W + MARGIN, y1 = H * (0.92 + yBias) + MARGIN * 0.3;
      const cx = x0 + (x1 - x0) * t01;
      const cy = y0 + (y1 - y0) * t01;
      const dx = (x1 - x0), dy = (y1 - y0);
      const len = Math.hypot(dx, dy);
      const ux = dx / len, uy = dy / len;
      const segLen = len * 0.16; // visible length of the blade itself

      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      for (let i = 0; i < TRAILS; i++) {
        const back = i * 5.5; // trailing offset along the stroke direction
        const alpha = (1 - i / TRAILS) * 0.75;
        ctx.strokeStyle = `rgba(245, 245, 240, ${alpha.toFixed(3)})`;
        ctx.lineWidth = WIDTH * (1 - i / (TRAILS * 1.6));
        ctx.beginPath();
        ctx.moveTo(cx - ux * back, cy - uy * back);
        ctx.lineTo(cx + ux * segLen - ux * back, cy + uy * segLen - uy * back);
        ctx.stroke();
      }
    }

    const startTime = performance.now();

    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      window.removeEventListener("resize", fit);
      canvas.remove();
      if (onComplete) onComplete();
    };

    let fading = false;
    function beginFade() {
      if (fading) return; // the rAF path and the setTimeout backstop both call this
      fading = true;
      canvas.style.transition = `opacity ${FADE}ms ease-out`;
      canvas.style.opacity = "0";
      canvas.addEventListener("transitionend", cleanup, { once: true });
      setTimeout(cleanup, FADE + 80); // safety net if transitionend never fires
    }

    function frame(now) {
      const elapsed = now - startTime;
      ctx.clearRect(0, 0, W, H);

      if (elapsed < actionEnd) {
        for (const s of strokes) {
          const local = elapsed - s.start;
          if (local < 0 || local > TRAVEL) continue;
          drawStroke(local / TRAVEL, s.yBias);
        }
        requestAnimationFrame(frame);
        return;
      }

      beginFade();
    }

    // Independent of rAF ever firing at all — a backgrounded tab suspends
    // requestAnimationFrame outright, which would otherwise leave this
    // overlay stuck at full opacity over the menu indefinitely. setTimeout
    // still runs (throttled, not stopped) in a background tab, so this is
    // the actual backstop; the transitionend one above only covers the much
    // narrower case where rAF was running fine but the CSS transition itself
    // never completed.
    setTimeout(beginFade, actionEnd + FADE + 1000);

    playWhoosh();
    requestAnimationFrame(frame);
  }
})();
