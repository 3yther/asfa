// Brush-stroke hover for the samurai menu. Each item gets its own procedural
// SVG stroke, generated at the label's measured width (so "Mission Control"
// and "Gym" each get a stroke drawn for their length rather than one path
// stretched), and revealed with a clip-path wipe. A second, dark copy of the
// label is clipped by the same wipe, so the inversion is spatial: dark glyphs
// exist only where white paint already is.
(function () {
  const menu = document.querySelector('.samurai-menu');
  if (!menu) return;
  const items = Array.from(menu.querySelectorAll('a'));

  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // One bristle bundle: a band with wavering edges, a thickness envelope that
  // lands heavy and lifts light, and a taper into the end.
  function bandPath(x0, x1, yc, th, rnd, env) {
    const L = x1 - x0;
    const k = Math.max(6, Math.round(L / 16));
    const top = [], bot = [];
    for (let j = 0; j <= k; j++) {
      const t = j / k;
      const x = x0 + L * t;
      const w = th * env(t) * (1 - 0.8 * Math.pow(t, 5));
      const wob = (rnd() - 0.5) * th * 0.38;
      top.push([x, yc - w / 2 + wob]);
      bot.push([x, yc + w / 2 + wob * 0.55]);
    }
    let d = 'M' + top[0][0].toFixed(1) + ',' + top[0][1].toFixed(1);
    for (let j = 1; j < top.length; j++) d += 'L' + top[j][0].toFixed(1) + ',' + top[j][1].toFixed(1);
    for (let j = bot.length - 1; j >= 0; j--) d += 'L' + bot[j][0].toFixed(1) + ',' + bot[j][1].toFixed(1);
    return d + 'Z';
  }

  function brush(W, H, seed) {
    const rnd = mulberry32(seed);
    const rb = (a, b) => a + rnd() * (b - a);
    // Heavy landing, full pull, light lift.
    const env = (t) => t < 0.08 ? 0.85 + (t / 0.08) * 0.3
      : t < 0.72 ? 1.15 - 0.12 * ((t - 0.08) / 0.64)
      : 1.03 - 0.6 * ((t - 0.72) / 0.28);
    const flat = () => 1;
    let body = '';

    // Bundles across the height. The glyph zone (middle ~40%) is covered by
    // dense, near-opaque bands so the inverted text always has paint under it;
    // the texture, gaps and short bristles live above and below it and at the
    // ends, where they overshoot the word.
    const n = 11 + Math.floor(rnd() * 4);
    for (let i = 0; i < n; i++) {
      const f = i / (n - 1);
      const core = Math.abs(f - 0.5) < 0.24;
      const yc = H * (0.18 + 0.64 * f) + (rnd() - 0.5) * H * 0.04;
      const th = H * (core ? rb(0.14, 0.21) : rb(0.05, 0.12));
      const x0 = W * (core ? rb(0.0, 0.03) : rb(0.0, 0.09));
      const x1 = W * (core ? rb(0.88, 0.995) : rb(0.58, 0.98));
      const op = core ? rb(0.88, 0.97) : rb(0.26, 0.62);
      body += '<path d="' + bandPath(x0, x1, yc, th, rnd, env) + '" opacity="' + op.toFixed(2) + '"/>';
    }
    // Loose hairs trailing off the lift.
    for (let i = 0; i < 5; i++) {
      const yc = H * rb(0.2, 0.8);
      const x0 = W * rb(0.7, 0.9);
      body += '<path d="' + bandPath(x0, Math.min(W, x0 + W * rb(0.06, 0.16)), yc, rb(0.8, 2.0), rnd, flat) + '" opacity="' + rb(0.3, 0.7).toFixed(2) + '"/>';
    }
    // Fine hair texture along the body.
    for (let i = 0; i < 10; i++) {
      const yc = H * rb(0.16, 0.84);
      const x0 = W * rb(0, 0.35);
      body += '<path d="' + bandPath(x0, Math.min(W, x0 + W * rb(0.2, 0.7)), yc, rb(0.5, 1.3), rnd, flat) + '" opacity="' + rb(0.18, 0.45).toFixed(2) + '"/>';
    }
    // Skips: places the brush lifted. Kept out of the glyph zone.
    let skips = '';
    for (let i = 0; i < 4; i++) {
      const yc = rnd() < 0.5 ? H * rb(0.14, 0.31) : H * rb(0.69, 0.86);
      const x0 = W * rb(0.12, 0.72);
      skips += '<path d="' + bandPath(x0, x0 + W * rb(0.05, 0.22), yc, rb(0.8, 2.4), rnd, flat) + '" fill="black" opacity="' + rb(0.55, 0.9).toFixed(2) + '"/>';
    }
    const id = 'sk' + seed;
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" preserveAspectRatio="none" aria-hidden="true">'
      + '<defs><mask id="' + id + '" maskUnits="userSpaceOnUse" x="0" y="0" width="' + W + '" height="' + H + '">'
      + '<rect width="' + W + '" height="' + H + '" fill="white"/>' + skips + '</mask></defs>'
      + '<g mask="url(#' + id + ')" fill="#f5f4ef">' + body + '</g></svg>';
  }

  function el(tag, cls) { const e = document.createElement(tag); e.className = cls; return e; }

  function build(a, i) {
    const text = a.textContent.trim();
    a.textContent = '';
    const stroke = el('span', 'stroke');
    stroke.setAttribute('aria-hidden', 'true');
    const base = el('span', 'lbl');
    base.textContent = text;
    const ink = el('span', 'lbl ink');
    ink.textContent = text;
    ink.setAttribute('aria-hidden', 'true');
    a.append(stroke, base, ink);
    a.dataset.seed = String(1000 + i * 7919);
  }

  function layout() {
    for (const a of items) {
      const base = a.querySelector('.lbl:not(.ink)');
      const r = base.getBoundingClientRect();
      const ar = a.getBoundingClientRect();
      const fs = parseFloat(getComputedStyle(a).fontSize);
      // Overshoot past the word both ends; taller than the cap height.
      const over = fs * 1.0;
      const H = fs * 2.2;
      const W = Math.round(r.width + over * 2);
      const stroke = a.querySelector('.stroke');
      stroke.style.left = (r.left - ar.left - over) + 'px';
      stroke.style.top = ((ar.height - H) / 2) + 'px';
      stroke.style.width = W + 'px';
      stroke.style.height = H + 'px';
      stroke.innerHTML = brush(W, Math.round(H), Number(a.dataset.seed));
    }
  }

  items.forEach(build);
  layout();

  // Exit choreography: the stroke leaves in the direction it came, and the
  // ink leads it out so the retreating edge never shows dark text on sky.
  for (const a of items) {
    const stroke = a.querySelector('.stroke');
    const leave = () => {
      if (a.matches(':hover') || a.matches(':focus-visible')) return;
      a.classList.remove('is-hot');
      a.classList.add('is-leaving');
    };
    a.addEventListener('pointerenter', () => a.classList.remove('is-leaving'));
    a.addEventListener('focus', () => a.classList.remove('is-leaving'));
    a.addEventListener('pointerleave', leave);
    a.addEventListener('blur', leave);
    // Touch has no hover: paint on press.
    a.addEventListener('pointerdown', () => { a.classList.remove('is-leaving'); a.classList.add('is-hot'); });
    a.addEventListener('pointerup', leave);
    a.addEventListener('pointercancel', leave);
    stroke.addEventListener('transitionend', () => {
      if (!a.classList.contains('is-leaving')) return;
      // Snap back to the resting clip with no transition, ready to re-enter.
      a.classList.add('no-anim');
      a.classList.remove('is-leaving');
      requestAnimationFrame(() => requestAnimationFrame(() => a.classList.remove('no-anim')));
    });
  }

  let t;
  window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(layout, 150); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(layout);
})();
