import * as THREE from 'three';

// Three altitude bands drifting independently — far/slow/faint sky ambience,
// a mid deck that catches the key light, and a near band that occasionally
// crosses the frame. Y ranges follow the brief; `distance`/`spread` do not —
// they're derived from this scene's actual camera (low, at plume height,
// pitched up ~9° and yawed ~17°; see samurai-theme.js CONFIG.camera). A
// literal Y=200 cloud only ~200 units away sits 40°+ above the lens and
// clips straight out of the 52° FOV; at the distances below, each layer's
// worst-case elevation lands comfortably inside frame (far ~24-29°, mid
// ~18-23°, near ~5-14°). `distance`/`spread` are measured along/around the
// camera's own forward direction (not raw world X/Z) so the field is
// actually centred on what the lens is pointed at, not on world-origin —
// the two differ here because of that ~17° yaw. The camera never moves, so
// this only had to be right once.
export const CLOUDS = {
  atlas: { cols: 4, rows: 2, cellPx: 128 },
  layers: [
    {
      key: 'far', count: 14,
      yRange: [190, 215], distance: 395, spread: 150,
      sizeRange: [58, 92], opacityRange: [0.15, 0.25],
      driftSpeed: 0.3, bobAmount: 0, bobPeriod: 0,
      litColor: 0xffffff, shadowColor: 0x9aa6a8, glow: 0.12,
    },
    {
      key: 'mid', count: 9,
      yRange: [70, 108], distance: 250, spread: 100,
      sizeRange: [27, 44], opacityRange: [0.36, 0.58],
      driftSpeed: 0.8, bobAmount: 5, bobPeriod: 15,
      litColor: 0xfff4dc, shadowColor: 0x5f5648, glow: 0.30,
    },
    {
      key: 'near', count: 5,
      yRange: [16, 33], distance: 140, spread: 45,
      sizeRange: [11, 17], opacityRange: [0.55, 0.75],
      driftSpeed: 1.2, bobAmount: 0, bobPeriod: 0,
      litColor: 0xf2f6f5, shadowColor: 0x3f4d50, glow: 0.16,
    },
  ],
};

// ── Cloud atlas: Perlin-style value noise -> threshold -> blur -> radial
// edge fade -> CanvasTexture. Runs once at boot; every layer's instances
// sample the same shared atlas via a per-instance tile index, so the cost is
// one small canvas fill, not one per cloud. ──────────────────────────────

function hash2(ix, iy, seed) {
  let h = (ix * 374761393 + iy * 668265263 + seed * 2246822519) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h = h ^ (h >>> 16);
  return ((h >>> 0) % 1000003) / 1000003;
}
function vnoise2(x, y, seed) {
  const x0 = Math.floor(x), y0 = Math.floor(y);
  const fx = x - x0, fy = y - y0;
  const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
  const n00 = hash2(x0, y0, seed), n10 = hash2(x0 + 1, y0, seed);
  const n01 = hash2(x0, y0 + 1, seed), n11 = hash2(x0 + 1, y0 + 1, seed);
  const a = n00 + (n10 - n00) * sx, b = n01 + (n11 - n01) * sx;
  return a + (b - a) * sy;
}
function fbm2(x, y, seed) {
  let a = 0.52, v = 0, f = 1;
  for (let o = 0; o < 4; o++) { v += a * vnoise2(x * f, y * f, seed + o * 101); f *= 2.03; a *= 0.5; }
  return v;
}
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// One atlas cell: fbm noise field, smoothstep-thresholded into a bulbous
// silhouette, blurred for soft interior edges, then multiplied by a radial
// mask so the tile always fades to zero alpha at its border — no seam under
// ClampToEdge sampling regardless of where the noise threshold lands.
function paintCell(ctx, ox, oy, N, rnd, seed) {
  const img = ctx.createImageData(N, N);
  const scale = 3.4 + rnd() * 1.6;
  const thresh = 0.46 + rnd() * 0.08;
  const offX = rnd() * 40, offY = rnd() * 40;
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const u = x / N, v = y / N;
      const n = fbm2(u * scale + offX, v * scale + offY, seed);
      let a = Math.max(0, Math.min(1, (n - thresh) / 0.22));
      a = a * a * (3 - 2 * a);
      const i = (y * N + x) * 4;
      img.data[i] = 255; img.data[i + 1] = 255; img.data[i + 2] = 255;
      img.data[i + 3] = Math.round(a * 255);
    }
  }
  const cell = document.createElement('canvas');
  cell.width = N; cell.height = N;
  const cctx = cell.getContext('2d');
  cctx.putImageData(img, 0, 0);

  // The radial edge-fade mask must be applied to this cell's own N×N canvas,
  // not the shared atlas: destination-in clears every pixel the new shape
  // doesn't cover, and a fillRect covering only this cell's region would
  // wipe every other already-painted cell on the atlas along with it.
  cctx.save();
  cctx.globalCompositeOperation = 'destination-in';
  const g = cctx.createRadialGradient(N / 2, N / 2, N * 0.16, N / 2, N / 2, N * 0.5);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.72, 'rgba(255,255,255,1)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  cctx.fillStyle = g;
  cctx.fillRect(0, 0, N, N);
  cctx.restore();

  // Draw the finished (masked) cell onto the shared atlas with a blur — the
  // "threshold, then smooth" softening — using plain source-over, which is
  // the only thing touching the atlas canvas.
  ctx.save();
  ctx.filter = `blur(${(N * 0.035).toFixed(1)}px)`;
  ctx.drawImage(cell, ox, oy);
  ctx.restore();
}

function buildAtlas(cols, rows, cellPx, seed) {
  const canvas = document.createElement('canvas');
  canvas.width = cols * cellPx;
  canvas.height = rows * cellPx;
  const ctx = canvas.getContext('2d');
  const rnd = mulberry32(seed);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) paintCell(ctx, c * cellPx, r * cellPx, cellPx, rnd, seed + r * cols + c + 1);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.minFilter = THREE.LinearFilter;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Per-instance placement, computed once. Rotation is baked at creation time
// (a lookAt toward the camera, which never moves in this scene, plus a
// random roll) — only translation needs to change per frame for drift/bob.
//
// Scattered in a disk around the point `distance` units out along the
// camera's own forward direction (projected flat, since altitude is handled
// separately/absolutely), using {right, forward} as the disk's basis so
// later drift can slide instances along `right` — i.e. sideways across the
// view — regardless of the camera's yaw.
function placeLayer(cfg, camera) {
  const dummy = new THREE.Object3D();
  const items = [];

  const forward = new THREE.Vector3();
  camera.getWorldDirection(forward);
  forward.y = 0;
  forward.normalize();
  const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 1, 0)).normalize();
  const center = camera.position.clone().addScaledVector(forward, cfg.distance);

  for (let i = 0; i < cfg.count; i++) {
    const y = cfg.yRange[0] + Math.random() * (cfg.yRange[1] - cfg.yRange[0]);
    // sqrt keeps the disk evenly covered rather than clumped at its centre.
    const r = Math.sqrt(Math.random()) * cfg.spread;
    const a = Math.random() * Math.PI * 2;
    const rightOffset = Math.cos(a) * r;
    const fwdOffset = Math.sin(a) * r;
    const size = cfg.sizeRange[0] + Math.random() * (cfg.sizeRange[1] - cfg.sizeRange[0]);
    const stretch = 0.75 + Math.random() * 0.5;

    const pos = center.clone().addScaledVector(right, rightOffset).addScaledVector(forward, fwdOffset);
    pos.y = y;
    dummy.position.copy(pos);
    dummy.lookAt(camera.position);
    dummy.rotateZ(Math.random() * Math.PI * 2);

    items.push({
      // Depth offset (along `forward`) is fixed per instance; only the
      // sideways offset (along `right`) advances over time, avoiding the
      // perspective-scale changes a depth drift would cause.
      rightOffset, fwdOffset, y, size, stretch,
      quat: dummy.quaternion.clone(),
      opacity: cfg.opacityRange[0] + Math.random() * (cfg.opacityRange[1] - cfg.opacityRange[0]),
      tint: Math.random(),
      bobPhase: Math.random() * Math.PI * 2,
    });
  }
  return { items, center, right, forward };
}

function buildLayerMaterial(cfg, atlas, sunDir, cols, rows) {
  const uniforms = {
    uSunDir: { value: sunDir.clone().normalize() },
    uLit: { value: new THREE.Color(cfg.litColor) },
    uShadow: { value: new THREE.Color(cfg.shadowColor) },
    uGlow: { value: cfg.glow },
  };
  const mat = new THREE.MeshStandardMaterial({
    map: atlas,
    roughness: 1, metalness: 0,
    transparent: true, alphaTest: 0.08, depthWrite: false,
    side: THREE.DoubleSide,
    // Clouds sit hundreds of units up; the ground-hugging fog (near 3 / far
    // 16, tuned to dissolve the grass into the horizon) would otherwise
    // crush anything this far away to solid fog colour.
    fog: false,
  });
  mat.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>
        attribute float aOpacity;
        attribute float aTint;
        attribute vec2 aTile;
        varying float vOpacity;
        varying float vTint;
        varying vec3 vWorldPos;`)
      // MeshStandardMaterial's own <map_fragment> samples `map` at vMapUv, not
      // vUv (vUv is unused for texturing in this material) — appending after
      // the stock chunk (rather than replacing it) keeps every other varying
      // it sets up, then remaps vMapUv from whole-atlas 0..1 into this
      // instance's one cell.
      .replace('#include <uv_vertex>', `#include <uv_vertex>
        #ifdef USE_MAP
          vMapUv = ( vMapUv + aTile ) / vec2( ${cols.toFixed(1)}, ${rows.toFixed(1)} );
        #endif`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        vOpacity = aOpacity;
        vTint = aTint;
        #ifdef USE_INSTANCING
          vWorldPos = ( modelMatrix * instanceMatrix * vec4( transformed, 1.0 ) ).xyz;
        #else
          vWorldPos = ( modelMatrix * vec4( transformed, 1.0 ) ).xyz;
        #endif`);

    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
        uniform vec3 uSunDir;
        uniform vec3 uLit;
        uniform vec3 uShadow;
        uniform float uGlow;
        varying float vOpacity;
        varying float vTint;
        varying vec3 vWorldPos;`)
      .replace('#include <color_fragment>', `#include <color_fragment>
        // Backlit-glow term, identical geometry to the susuki translucency:
        // large when the camera looks roughly back toward the sun through
        // the billboard. Also drives the lit/shadow colour mix per fragment.
        float back = pow( max( dot( normalize( cameraPosition - vWorldPos ), -uSunDir ), 0.0 ), 2.0 );
        vec3 base = mix( uShadow, uLit, clamp( back * 1.6, 0.0, 1.0 ) );
        diffuseColor.rgb = base * mix( 0.92, 1.06, vTint );
        diffuseColor.a *= vOpacity;`)
      .replace('#include <opaque_fragment>', `
        float back2 = pow( max( dot( normalize( cameraPosition - vWorldPos ), -uSunDir ), 0.0 ), 2.0 );
        outgoingLight += uLit * back2 * uGlow * diffuseColor.a;
        #include <opaque_fragment>`);
  };
  return mat;
}

function buildLayer(cfg, atlas, sunDir, camera, atlasCfg) {
  const { items, center, right, forward } = placeLayer(cfg, camera);
  const geo = new THREE.PlaneGeometry(1, 1);

  const aOpacity = new Float32Array(cfg.count);
  const aTint = new Float32Array(cfg.count);
  const aTile = new Float32Array(cfg.count * 2);
  for (let i = 0; i < cfg.count; i++) {
    aOpacity[i] = items[i].opacity;
    aTint[i] = items[i].tint;
    aTile[i * 2] = Math.floor(Math.random() * atlasCfg.cols);
    aTile[i * 2 + 1] = Math.floor(Math.random() * atlasCfg.rows);
  }
  geo.setAttribute('aOpacity', new THREE.InstancedBufferAttribute(aOpacity, 1));
  geo.setAttribute('aTint', new THREE.InstancedBufferAttribute(aTint, 1));
  geo.setAttribute('aTile', new THREE.InstancedBufferAttribute(aTile, 2));

  const material = buildLayerMaterial(cfg, atlas, sunDir, atlasCfg.cols, atlasCfg.rows);
  const mesh = new THREE.InstancedMesh(geo, material, cfg.count);
  mesh.frustumCulled = false;
  mesh.castShadow = false;
  mesh.receiveShadow = false;

  const m = new THREE.Matrix4();
  const pos = new THREE.Vector3();
  const scale = new THREE.Vector3();
  const range = cfg.spread * 2;
  const twoPiOverPeriod = cfg.bobPeriod > 0 ? (2 * Math.PI) / cfg.bobPeriod : 0;

  function place(i, t) {
    const it = items[i];
    // Drift is a wrapped offset along `right` (sideways across the view);
    // `fwdOffset` (depth) and `y` (absolute altitude) stay fixed per instance.
    const driftedRight = ((it.rightOffset + t * cfg.driftSpeed + cfg.spread) % range + range) % range - cfg.spread;
    pos.copy(center)
      .addScaledVector(right, driftedRight)
      .addScaledVector(forward, it.fwdOffset);
    pos.y = it.y + (cfg.bobAmount > 0 ? Math.sin(t * twoPiOverPeriod + it.bobPhase) * cfg.bobAmount : 0);
    scale.set(it.size * it.stretch, it.size, 1);
    m.compose(pos, it.quat, scale);
    mesh.setMatrixAt(i, m);
  }

  for (let i = 0; i < cfg.count; i++) place(i, 0);
  mesh.instanceMatrix.needsUpdate = true;

  function update(dt, t) {
    for (let i = 0; i < cfg.count; i++) place(i, t);
    mesh.instanceMatrix.needsUpdate = true;
  }

  return { mesh, update };
}

export function createClouds(sun, camera) {
  const atlasCfg = CLOUDS.atlas;
  const atlas = buildAtlas(atlasCfg.cols, atlasCfg.rows, atlasCfg.cellPx, 4177);
  const sunDir = sun.position;

  const group = new THREE.Group();
  const layers = CLOUDS.layers.map((cfg) => buildLayer(cfg, atlas, sunDir, camera, atlasCfg));
  for (const l of layers) group.add(l.mesh);

  group.userData.update = (dt, t) => { for (const l of layers) l.update(dt, t); };
  group.userData.dispose = () => {
    atlas.dispose();
    for (const l of layers) { l.mesh.geometry.dispose(); l.mesh.material.dispose(); }
  };
  return group;
}
