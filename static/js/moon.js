import * as THREE from 'three';

// The moon the dashboard palette keeps claiming to be lit by. Warm cream rather
// than a stark white disc, so it belongs to the same family as the blade's rim
// light and the cloud deck's lit edge instead of punching a cold hole in the sky.
export const MOON = {
  // Placed by where it should land in frame, not by world coordinates: an NDC
  // point in the upper right, unprojected through the camera. Solving it this
  // way means the moon stays put if the camera is ever re-framed, and it cannot
  // drift behind the katana (which stands at +x but low) or into the menu's
  // left column.
  ndc: [0.56, 0.60],
  // Beyond the mid cloud deck (250) and inside the sky dome (400), so roughly
  // half the far band's instances — which sit at 245..545 — drift in front of
  // it and the rest behind. That is what does the partial-obscuring; nothing
  // is scripted to cross it.
  distance: 355,
  radius: 15.5,
  color: 0xe6dcbe,
  // Thin mist drawn just in front of the disc. The real cloud deck does cross
  // the moon — measured, a mid-band instance sweeps it from t≈136s to t≈176s
  // and takes the disc from 0.918 to 0.80 luminance — but that band holds only
  // nine instances, so it happens for ~12% of its 250 s drift cycle and the
  // moon sits bare the rest of the time. These two wisps are what keep it
  // "never fully clear" between crossings; the deck still supplies the heavier
  // veil when it comes round. Deliberately in this module and not in clouds.js,
  // which the brief puts off limits.
  // scaleY is well over 1 on purpose. At 1.15 the wisp was barely taller than
  // the disc, so its own top and bottom falloff landed *on* the moon and drew a
  // visible arc across it — the wisp has to overhang far enough that only its
  // gentle middle ever crosses the face.
  veils: [
    { depthOffset: 14, scaleX: 3.4, scaleY: 2.5, opacity: 0.30, speed: 0.9, phase: 0.0 },
    { depthOffset: 24, scaleX: 4.6, scaleY: 3.3, opacity: 0.20, speed: 0.55, phase: 2.1 },
  ],
  veilColor: 0x74828a,
  veilTravel: 90,     // world units the wisps traverse before wrapping
  // Chosen by sweeping it and reading the disc back off the framebuffer. The
  // measured trade is entirely about clipping: 1.45 renders (255,255,224) with
  // red and green both pinned, so a warm moon comes out stark white; 1.15
  // renders (245,234,202) with nothing clipped and the widest R−B spread of any
  // value tried (43 vs 31), i.e. the warmest the moon can actually look. At
  // 1.95 the whole sky lifted a stop with it.
  //
  // Neither value reaches UnrealBloomPass's 1.15 luminance threshold — that
  // would need ~2.2, which is deep into clipping — so the glow below is doing
  // that job rather than the bloom pass.
  emissive: 1.15,
  haloScale: 5.6,
  haloOpacity: 0.34,
  haloColor: 0xd8cfae,
};

// Radial falloff for the halo billboard, as a texture rather than a shader:
// one 128px canvas costs nothing and keeps the material a plain sprite.
// pow(1-r, 3) rather than a linear ramp — a linear halo reads as a visible disc
// with an edge, which is exactly what the moon should not have.
function haloTexture(px = 128) {
  const c = document.createElement('canvas');
  c.width = c.height = px;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(px, px);
  const mid = (px - 1) / 2;
  for (let y = 0; y < px; y++) {
    for (let x = 0; x < px; x++) {
      const r = Math.min(1, Math.hypot(x - mid, y - mid) / mid);
      const a = Math.pow(1 - r, 3) * (1 - r * 0.15);
      const i = (y * px + x) * 4;
      img.data[i] = 255; img.data[i + 1] = 255; img.data[i + 2] = 255;
      img.data[i + 3] = Math.round(Math.max(0, a) * 255);
    }
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// The disc itself: solid through the middle, with the last ~14% of the radius
// rolled off to nothing. A sphere gave a geometrically hard limb — an unlit
// MeshBasicMaterial sphere is a flat disc with a cut edge — which is precisely
// the "sharp edge" the moon is not supposed to have. Drawn instead as a
// camera-facing billboard, which also drops 32×24 sphere segments for two
// triangles. Safe here only because this camera never moves.
function discTexture(px = 256) {
  const c = document.createElement('canvas');
  c.width = c.height = px;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(px, px);
  const mid = (px - 1) / 2;
  for (let y = 0; y < px; y++) {
    for (let x = 0; x < px; x++) {
      const r = Math.hypot(x - mid, y - mid) / mid;
      // Smooth roll-off across the limb, plus a very slight dimming toward it
      // so the disc reads as a sphere lit flat rather than as a paper cut-out.
      const edge = 1 - Math.min(1, Math.max(0, (r - 0.84) / 0.16));
      const a = edge * edge * (3 - 2 * edge);
      const limb = 1 - 0.10 * Math.pow(Math.min(r / 0.9, 1), 3);
      const i = (y * px + x) * 4;
      const v = Math.round(255 * limb);
      img.data[i] = v; img.data[i+1] = v; img.data[i+2] = v;
      img.data[i+3] = Math.round(a * 255);
    }
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// A soft horizontal streak, densest through the middle and fading to nothing at
// every edge. Feathered on all four sides so a wisp can never present a straight
// boundary across the disc — a hard edge would read as a bar, not as mist.
function veilTexture(w = 256, h = 64) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(w, h);
  // Two offset sine ridges give the streak an uneven spine, so the two wisps do
  // not read as the same shape at different sizes.
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const u = x / w, v = y / h;
      const spine = 0.5 + 0.13 * Math.sin(u * 7.1) + 0.07 * Math.sin(u * 17.3 + 1.2);
      // Gentle exponents and a wide half-width: a steep ramp puts a readable
      // boundary wherever the wisp crosses the bright disc.
      const across = Math.max(0, 1 - Math.abs(v - spine) / 0.46);
      const along = Math.min(1, Math.min(u, 1 - u) / 0.30);
      const a = Math.pow(across, 1.15) * Math.pow(along, 1.1)
              * (0.78 + 0.22 * Math.sin(u * 11.7 + v * 5.0));
      const i = (y * w + x) * 4;
      img.data[i] = 255; img.data[i+1] = 255; img.data[i+2] = 255;
      img.data[i+3] = Math.round(Math.max(0, Math.min(1, a)) * 255);
    }
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

export function createMoon(camera) {
  const group = new THREE.Group();

  // Unproject the framing point onto a ray, then walk out along it. The
  // matrixWorld update is load-bearing: camera.lookAt() writes the quaternion
  // but nothing composes it into matrixWorld until the first render, and
  // unproject() reads matrixWorld — without this the moon is placed through an
  // identity transform and lands wherever that happens to point (in practice,
  // straight behind the sword).
  camera.updateMatrixWorld(true);
  const p = new THREE.Vector3(MOON.ndc[0], MOON.ndc[1], 0.5).unproject(camera);
  const dir = p.sub(camera.position).normalize();
  const pos = camera.position.clone().addScaledVector(dir, MOON.distance);

  // MeshBasicMaterial, not Standard: the moon is the light source in this
  // scene's fiction, so lighting it with the scene's own key light would be
  // backwards — and at 355 units the directional light contributes nothing
  // anyway. `fog: false` for the same reason the clouds set it: the ground fog
  // ends at 16 units and would otherwise render the moon as a flat fog-coloured
  // disc.
  const discTex = discTexture();
  const disc = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({
      map: discTex,
      color: new THREE.Color(MOON.color).multiplyScalar(MOON.emissive),
      transparent: true,
      depthWrite: false,
      fog: false,
      toneMapped: false,
    })
  );
  disc.position.copy(pos);
  disc.scale.setScalar(MOON.radius * 2);
  disc.quaternion.copy(camera.quaternion);
  disc.frustumCulled = false;
  disc.renderOrder = -1;
  group.add(disc);

  // Atmospheric glow around the disc — the part that sells "through the
  // atmosphere" rather than "in front of a backdrop". Additive and depth-tested
  // but not depth-writing, so cloud billboards nearer than the moon still draw
  // over it in the transparent pass.
  const halo = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({
      map: haloTexture(),
      color: MOON.haloColor,
      transparent: true,
      opacity: MOON.haloOpacity,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      fog: false,
      toneMapped: false,
    })
  );
  halo.position.copy(pos);
  halo.scale.setScalar(MOON.radius * MOON.haloScale);
  halo.quaternion.copy(camera.quaternion);   // the camera never moves in this scene
  halo.frustumCulled = false;
  // Explicit ordering through the transparent queue — halo, then disc, then the
  // wisps at 0, then the cloud deck's own instances. Left to distance sorting
  // these are all within a few units of each other and could swap.
  halo.renderOrder = -2;
  group.add(halo);

  // The wisps. Placed between the camera and the disc along the same ray, and
  // slid along the camera's own right vector so they track across the face of
  // the moon rather than through it at some arbitrary world angle.
  const right = new THREE.Vector3().crossVectors(dir, new THREE.Vector3(0, 1, 0)).normalize();
  const veilTex = veilTexture();
  const veils = MOON.veils.map((cfg) => {
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({
        map: veilTex,
        color: MOON.veilColor,
        transparent: true,
        opacity: cfg.opacity,
        depthWrite: false,
        fog: false,
        toneMapped: false,
      })
    );
    mesh.scale.set(MOON.radius * 2 * cfg.scaleX, MOON.radius * 2 * cfg.scaleY, 1);
    mesh.quaternion.copy(camera.quaternion);
    mesh.frustumCulled = false;
    mesh.renderOrder = 0;              // after the halo (-1), before the deck
    mesh.userData.cfg = cfg;
    group.add(mesh);
    return mesh;
  });

  const base = pos.clone();
  function update(dt, t) {
    for (const v of veils) {
      const cfg = v.userData.cfg;
      // Wrapped into [-travel/2, +travel/2] so a wisp re-enters from the far
      // side instead of ever popping out mid-frame.
      const span = MOON.veilTravel;
      const x = ((t * cfg.speed + cfg.phase * span + span / 2) % span + span) % span - span / 2;
      v.position.copy(base)
        .addScaledVector(dir, -cfg.depthOffset)
        .addScaledVector(right, x);
    }
  }
  update(0, 0);

  group.userData.update = update;
  group.userData.dispose = () => {
    disc.geometry.dispose(); disc.material.dispose(); discTex.dispose();
    halo.geometry.dispose(); halo.material.map.dispose(); halo.material.dispose();
    for (const v of veils) { v.geometry.dispose(); v.material.dispose(); }
    veilTex.dispose();
  };
  group.userData.debug = { position: pos.clone(), radius: MOON.radius };
  return group;
}
