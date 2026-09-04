/*
 * Cosmos menu boot — mounts the Gargantua render behind the menu.
 *
 * Same shader/module as the /command entrance (gargantua.js); the differences
 * are framing (pushed right of centre so the menu column owns the left), star
 * parallax on, and no warp/arrival choreography — this screen is persistent,
 * not a one-shot. Failure at any step reveals the CSS gradient instead, so the
 * menu is always usable.
 */
import { createGargantua } from "./gargantua.js";

const stage = document.getElementById("cosmos-stage");
const loader = document.getElementById("cosmos-loader");
const fallback = document.getElementById("cosmos-fallback");

const reduce = !!(window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches);

function hideLoader() {
  try { clearTimeout(window.__cosmosSafety); } catch (e) {}
  if (loader) loader.classList.add("hidden");
}
function showFallback() {
  if (fallback) fallback.classList.add("show");
  hideLoader();
}

// Below this width the menu moves to the lower third (see the media query),
// so the hole recentres rather than crowding it.
const narrow = (window.innerWidth || document.documentElement.clientWidth || 1280) < 820;

(async function boot() {
  try {
    const g = await createGargantua({
      container: stage,
      centerX: narrow ? 0.5 : 0.62,   // right-of-centre on desktop
      camDist: 11.0,
      reduce,
      parallax: true,
      bloom: [0.28, 0.34, 1.05],
    });

    // Expose the fall-through for cosmos-menu.js's click handler. Same
    // transition the entrance uses — reused, not reimplemented.
    window.__cosmosFallIn = (cb) => g.fallIn(cb);

    // ── Ambient idle redshift ────────────────────────────────────────────────
    // After a stretch of no interaction, cool the grade by a few percent. Slow
    // enough to be near-subliminal; any input snaps it back immediately.
    const IDLE_AFTER = 50000;
    let lastInput = performance.now();
    const bump = () => { lastInput = performance.now(); g.setRedshift(0); };
    ["pointermove", "pointerdown", "keydown", "wheel", "touchstart"].forEach((ev) =>
      window.addEventListener(ev, bump, { passive: true }));

    if (!reduce) {
      g.onFrame(() => {
        const idle = performance.now() - lastInput;
        if (idle > IDLE_AFTER) {
          const k = Math.min(1, (idle - IDLE_AFTER) / 30000);
          g.setRedshift(k);
        }
      });
    }

    hideLoader();
  } catch (e) {
    console.warn("[cosmos] WebGL unavailable — static fallback:", e);
    showFallback();
  }
})();

// ── Mission clock caption ────────────────────────────────────────────────────
// Counts from page load. Deadpan on purpose; it is flavour, not telemetry.
(function clock() {
  const el = document.getElementById("cosmos-clock");
  if (!el) return;
  const t0 = Date.now();
  const pad = (n) => String(n).padStart(2, "0");
  function tick() {
    const s = Math.floor((Date.now() - t0) / 1000);
    el.textContent = `T+${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
    setTimeout(tick, 1000);
  }
  tick();
})();
