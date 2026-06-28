// Decode-in effect: scrambles text briefly then resolves to real value.
// Characters cycle through random chars before settling into place,
// left-to-right, so static headings/labels "boot up" on page load.
const CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&';

function decodeIn(el, delay = 0) {
  const original = el.textContent.trim();
  if (!original || original.length > 40) return; // skip empty / long text

  let frame = 0;
  const totalFrames = 18;
  const scrambleFrames = 12;

  setTimeout(() => {
    const interval = setInterval(() => {
      if (frame >= totalFrames) {
        el.textContent = original;
        clearInterval(interval);
        return;
      }

      if (frame < scrambleFrames) {
        // Scramble phase — random chars resolving left-to-right
        el.textContent = original.split('').map((c, i) => {
          if (c === ' ') return ' ';
          if (frame / scrambleFrames > i / original.length) return c;
          return CHARS[Math.floor(Math.random() * CHARS.length)];
        }).join('');
      } else {
        el.textContent = original;
      }
      frame++;
    }, 40);
  }, delay);
}

// Apply to all elements with a data-decode attribute on load, staggered so
// they cascade rather than firing all at once.
document.addEventListener('DOMContentLoaded', () => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  document.querySelectorAll('[data-decode]').forEach((el, i) => {
    decodeIn(el, i * 80);
  });
});
