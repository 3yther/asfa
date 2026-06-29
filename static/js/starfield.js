/* starfield.js — Three.js animated deep-space starfield background.
 *
 * Renders into <canvas id="starfield-bg"> as a fixed, full-viewport,
 * non-interactive background layer (z-index -1). Requires THREE (r128)
 * to be loaded ahead of this script via a CDN <script> tag in the page
 * <head>. Degrades gracefully if THREE or WebGL is unavailable, and
 * honors prefers-reduced-motion by rendering a single static frame.
 */
(function () {
  "use strict";

  var canvas = document.getElementById("starfield-bg");
  if (!canvas) return;

  // Bail out quietly if Three.js never loaded.
  if (typeof THREE === "undefined") return;

  // Detect WebGL support before asking Three.js for a renderer; if the
  // context can't be created we leave the (transparent) canvas in place
  // and do nothing else.
  function webglAvailable() {
    try {
      var test = document.createElement("canvas");
      return !!(
        window.WebGLRenderingContext &&
        (test.getContext("webgl") || test.getContext("experimental-webgl"))
      );
    } catch (e) {
      return false;
    }
  }
  if (!webglAvailable()) return;

  var prefersReducedMotion =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var STAR_COUNT = 2500;
  var HERO_COUNT = 25;
  // One full rotation in ~10 minutes (600s). radians/sec.
  var ROTATION_SPEED = (Math.PI * 2) / 600;
  var PARALLAX_MAX = 20; // px of camera shift at the extremes

  var renderer, scene, camera;
  var stars, heroStars, nebulae = [];
  var heroPhases = [];
  var heroBaseOpacity;

  // Mouse parallax: target tracks the pointer, current is damped toward it.
  var parallaxTargetX = 0,
    parallaxTargetY = 0;
  var parallaxX = 0,
    parallaxY = 0;

  var clock = null;
  var rafId = null;
  var running = false;

  function rand(min, max) {
    return min + Math.random() * (max - min);
  }

  // Build a soft radial-gradient sprite texture for stars / nebulae.
  function makeGlowTexture(hexColor, edgeAlpha) {
    var size = 128;
    var c = document.createElement("canvas");
    c.width = c.height = size;
    var ctx = c.getContext("2d");
    var grad = ctx.createRadialGradient(
      size / 2,
      size / 2,
      0,
      size / 2,
      size / 2,
      size / 2
    );
    grad.addColorStop(0, "rgba(" + hexColor + ",1)");
    grad.addColorStop(0.25, "rgba(" + hexColor + ",0.6)");
    grad.addColorStop(1, "rgba(" + hexColor + "," + (edgeAlpha || 0) + ")");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, size, size);
    var tex = new THREE.Texture(c);
    tex.needsUpdate = true;
    return tex;
  }

  function buildStars() {
    var geo = new THREE.BufferGeometry();
    var positions = new Float32Array(STAR_COUNT * 3);
    var sizes = new Float32Array(STAR_COUNT);
    var radius = 600;

    for (var i = 0; i < STAR_COUNT; i++) {
      // Distribute on a spherical shell so rotation reads as deep space.
      var theta = Math.random() * Math.PI * 2;
      var phi = Math.acos(rand(-1, 1));
      var r = radius * rand(0.55, 1);
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[i * 3 + 2] = r * Math.cos(phi);
      sizes[i] = rand(0.2, 1.2);
    }

    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("size", new THREE.BufferAttribute(sizes, 1));

    // Per-vertex sizing via a tiny shader so the 0.2–1.2 size range and
    // the 0.2–0.8 opacity range render correctly at distance.
    var mat = new THREE.ShaderMaterial({
      uniforms: {
        uTexture: { value: makeGlowTexture("255,255,255", 0) },
        uOpacity: { value: 0.8 },
      },
      vertexShader: [
        "attribute float size;",
        "void main() {",
        "  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);",
        "  gl_PointSize = size * (300.0 / -mvPosition.z);",
        "  gl_Position = projectionMatrix * mvPosition;",
        "}",
      ].join("\n"),
      fragmentShader: [
        "uniform sampler2D uTexture;",
        "uniform float uOpacity;",
        "void main() {",
        "  vec4 tex = texture2D(uTexture, gl_PointCoord);",
        "  gl_FragColor = vec4(tex.rgb, tex.a * uOpacity);",
        "}",
      ].join("\n"),
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    stars = new THREE.Points(geo, mat);
    scene.add(stars);
  }

  function buildHeroStars() {
    var geo = new THREE.BufferGeometry();
    var positions = new Float32Array(HERO_COUNT * 3);
    var sizes = new Float32Array(HERO_COUNT);
    var radius = 520;

    for (var i = 0; i < HERO_COUNT; i++) {
      var theta = Math.random() * Math.PI * 2;
      var phi = Math.acos(rand(-1, 1));
      positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
      positions[i * 3 + 2] = radius * Math.cos(phi);
      sizes[i] = rand(2.5, 4.5);
      heroPhases.push(Math.random() * Math.PI * 2);
    }

    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("size", new THREE.BufferAttribute(sizes, 1));

    heroBaseOpacity = 0.9;
    var mat = new THREE.ShaderMaterial({
      uniforms: {
        uTexture: { value: makeGlowTexture("180,225,255", 0) },
        uOpacity: { value: heroBaseOpacity },
      },
      vertexShader: [
        "attribute float size;",
        "void main() {",
        "  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);",
        "  gl_PointSize = size * (300.0 / -mvPosition.z);",
        "  gl_Position = projectionMatrix * mvPosition;",
        "}",
      ].join("\n"),
      fragmentShader: [
        "uniform sampler2D uTexture;",
        "uniform float uOpacity;",
        "void main() {",
        "  vec4 tex = texture2D(uTexture, gl_PointCoord);",
        "  gl_FragColor = vec4(tex.rgb, tex.a * uOpacity);",
        "}",
      ].join("\n"),
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    heroStars = new THREE.Points(geo, mat);
    scene.add(heroStars);
  }

  function buildNebula(rgb, x, y, z, scale) {
    var mat = new THREE.SpriteMaterial({
      map: makeGlowTexture(rgb, 0),
      transparent: true,
      opacity: 0.06,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    var sprite = new THREE.Sprite(mat);
    sprite.position.set(x, y, z);
    sprite.scale.set(scale, scale, 1);
    sprite.userData.drift = rand(0.2, 0.5) * (Math.random() < 0.5 ? -1 : 1);
    scene.add(sprite);
    nebulae.push(sprite);
  }

  function init() {
    renderer = new THREE.WebGLRenderer({
      canvas: canvas,
      antialias: true,
      alpha: true,
    });
    renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(
      60,
      window.innerWidth / window.innerHeight,
      1,
      2000
    );
    camera.position.z = 0.1;

    buildStars();
    buildHeroStars();
    // Cyan and violet faint nebulae, placed deep and off-center, drifting.
    buildNebula("0,217,255", -260, 120, -400, 700); // #00d9ff
    buildNebula("127,119,221", 280, -150, -450, 800); // #7f77dd

    resize();
    clock = new THREE.Clock();

    window.addEventListener("resize", resize);
    window.addEventListener("mousemove", onMouseMove);
    document.addEventListener("visibilitychange", onVisibilityChange);

    if (prefersReducedMotion) {
      // Static field: render one frame and stop.
      renderer.render(scene, camera);
    } else {
      start();
    }
  }

  function resize() {
    var w = window.innerWidth;
    var h = window.innerHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(w, h, false);
    if (prefersReducedMotion && renderer) renderer.render(scene, camera);
  }

  function onMouseMove(e) {
    var nx = (e.clientX / window.innerWidth) * 2 - 1;
    var ny = (e.clientY / window.innerHeight) * 2 - 1;
    parallaxTargetX = nx * PARALLAX_MAX;
    parallaxTargetY = -ny * PARALLAX_MAX;
  }

  function onVisibilityChange() {
    if (document.hidden) {
      stop();
    } else if (!prefersReducedMotion) {
      start();
    }
  }

  function start() {
    if (running) return;
    running = true;
    if (clock) clock.start();
    rafId = requestAnimationFrame(animate);
  }

  function stop() {
    running = false;
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  function animate() {
    if (!running) return;
    rafId = requestAnimationFrame(animate);

    var dt = clock.getDelta();
    var t = clock.elapsedTime;

    // Very slow continuous rotation of the whole field.
    if (stars) stars.rotation.y += ROTATION_SPEED * dt;
    if (heroStars) {
      heroStars.rotation.y += ROTATION_SPEED * dt;
      // Twinkle hero stars via a sin() wave averaged across their phases,
      // modulating the shared point opacity between dim and bright.
      var tw = 0;
      for (var i = 0; i < heroPhases.length; i++) {
        tw += Math.sin(t * 1.8 + heroPhases[i]);
      }
      tw /= heroPhases.length; // averaged -1..1
      heroStars.material.uniforms.uOpacity.value =
        heroBaseOpacity * (0.65 + 0.35 * (0.5 + 0.5 * tw));
    }

    // Slow nebula drift.
    for (var n = 0; n < nebulae.length; n++) {
      var s = nebulae[n];
      s.position.x += s.userData.drift * dt;
      s.material.opacity = 0.06 + 0.015 * Math.sin(t * 0.2 + n);
    }

    // Damped mouse parallax on the camera.
    parallaxX += (parallaxTargetX - parallaxX) * 0.03;
    parallaxY += (parallaxTargetY - parallaxY) * 0.03;
    camera.position.x = parallaxX * 0.05;
    camera.position.y = parallaxY * 0.05;
    camera.lookAt(scene.position);

    renderer.render(scene, camera);
  }

  init();
})();
