import * as THREE from 'three';
import { SamuraiScene, CONFIG } from './samurai-theme.js';
import { createKatana } from './katana.js';
import { createGrassField } from './grass-shader.js';
import { createParticles } from './particles.js';
import { createClouds } from './clouds.js';

const canvas = document.getElementById('samurai-canvas');
const loader = document.getElementById('samurai-loader');
const fallback = document.getElementById('samurai-fallback');

function supportsWebGL() {
  try {
    return !!document.createElement('canvas').getContext('webgl2');
  } catch {
    return false;
  }
}

function reveal() {
  loader.classList.add('hidden');
  loader.addEventListener('transitionend', () => loader.remove(), { once: true });
}

if (!supportsWebGL()) {
  clearTimeout(window.__samuraiSafety);
  fallback.classList.add('show');
  loader.classList.add('hidden');
} else {
  // Cancel the fallback safety net as soon as WebGL init has structurally
  // succeeded — not inside reveal()'s rAF chain below. rAF is suspended
  // outright in a backgrounded/inactive tab (e.g. a link opened without
  // switching to it), so a tab that isn't focused within the first 9s of
  // load would otherwise never clear the timeout in time: the static
  // fallback (z-index 20) would then land on top of a scene that in fact
  // rendered fine, permanently covering the menu (z-index 10) once the tab
  // does come to the front. The loader's own reveal still waits for a real
  // frame (below) — only the safety-net cancellation needed decoupling.
  clearTimeout(window.__samuraiSafety);
  const app = new SamuraiScene(canvas).init();

  // Clouds: transparent, so Three's own opaque-then-transparent pass with
  // depth-testing already puts them behind the grass/katana silhouette and
  // in front of the sky dome (which never writes depth) — no explicit
  // renderOrder needed. Added here, before the opaque scene content, purely
  // to keep scene-graph order matching that visual layering.
  const clouds = createClouds(app.sun, app.camera);
  app.scene.add(clouds);
  app.add({ update: clouds.userData.update, dispose: clouds.userData.dispose });

  // The katana group carries its own point-down flip; the lean goes on a
  // pivot above it so the two rotations compose instead of overwriting.
  const katanaPivot = new THREE.Group();
  katanaPivot.add(createKatana(CONFIG.colors.sunGlow));
  katanaPivot.position.set(...CONFIG.katana.position);
  katanaPivot.rotation.z = CONFIG.katana.lean;
  app.scene.add(katanaPivot);

  const grass = createGrassField(app.sun, CONFIG.colors.sunGlow, app.camera.position);
  app.scene.add(grass);
  app.add({ update: grass.userData.update });

  const particles = createParticles(app.scene.fog, app.sun.position, CONFIG.colors.sunGlow);
  app.scene.add(particles);
  app.add({ update: particles.userData.update, dispose: particles.userData.dispose });

  app.start();
  window.samurai = app;
  // Reveal on the second frame so the first render is already on the canvas.
  requestAnimationFrame(() => requestAnimationFrame(reveal));
}
