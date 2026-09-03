import { SamuraiScene } from './samurai-theme.js';
import { createKatana } from './katana.js';
import { createGrassField } from './grass-shader.js';
import { createParticles } from './particles.js';

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
  clearTimeout(window.__samuraiSafety);
  loader.classList.add('hidden');
  loader.addEventListener('transitionend', () => loader.remove(), { once: true });
}

if (!supportsWebGL()) {
  clearTimeout(window.__samuraiSafety);
  fallback.classList.add('show');
  loader.classList.add('hidden');
} else {
  const app = new SamuraiScene(canvas).init();
  app.scene.add(createKatana());

  const grass = createGrassField();
  app.scene.add(grass);
  app.add({ update: grass.userData.update });

  const particles = createParticles(app.scene.fog);
  app.scene.add(particles);
  app.add({ update: particles.userData.update, dispose: particles.userData.dispose });

  app.start();
  window.samurai = app;
  // Reveal on the second frame so the first render is already on the canvas.
  requestAnimationFrame(() => requestAnimationFrame(reveal));
}
