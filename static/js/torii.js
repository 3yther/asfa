import * as THREE from 'three';
import { CONFIG, SKY_GLOW_GLSL } from './samurai-theme.js';

// A shrine gate standing off in the mist, well left of the katana. It is meant
// to be found rather than noticed: a silhouette at the edge of visibility that
// rewards a second look and never competes with the blade.
//
// Proportions follow a myojin torii — the style with the upward-curving lintel.
// Everything is a cylinder or a box; at this distance the silhouette is the
// only thing that survives, so modelling detail into it would be wasted.
export const TORII = {
  // Framed, not positioned. The gate is placed by solving for the world point
  // that lands at `ndcX` on the horizon at `distance` — because a fixed world
  // coordinate only frames correctly at one aspect ratio. A hardcoded
  // [-4.2, 0, -27] sat at NDC -0.50 on a 16:9 desktop and at **-1.86** on a
  // 375×812 phone, i.e. completely off the side of the screen, since portrait
  // narrows the horizontal field of view. The moon never had this problem
  // because it was always placed by unprojection.
  //
  // -0.50 puts it in the gap between the menu column's right edge (measured at
  // -0.54 off the longest item, "Mission Control") and the sword at ~0.0.
  ndcX: -0.50,
  distance: 27,             // along the camera's flattened forward axis
  // Portrait has no free column: the menu runs down the left of the whole
  // screen, so there is no gap to drop the gate into the way there is on a wide
  // viewport. Standing it further back shrinks it until only the lintel clears
  // the top menu item, and pushing it further left keeps its posts off the
  // longest labels — it reads as more distant rather than as an obstruction.
  portrait: { aspectBelow: 1.2, ndcX: -0.63, distance: 40 },
  rotationY: 0.42,          // turned slightly toward the camera, so it reads as a gate
  height: 6.8,              // top of the kasagi, in world units
  span: 4.4,                // centre-to-centre of the two posts
  postRadius: 0.24,
  postTaper: 0.80,          // top radius as a fraction of the base — hashira taper
  color: 0x1b2327,
  // The colour distance washes it toward. NOT CONFIG.colors.fog (#5a6a6d):
  // that is the ground-fog slate, tuned for the horizon band, and it is
  // brighter than the cloud deck this gate stands against — veiling toward it
  // made the torii a pale smear rather than a dark one, measurably brighter
  // than the sky beside it. This is the deck's own dark, so the mix can only
  // ever lighten the gate toward what is actually behind it.
  veilColor: 0x2c383c,
  // How much of that is mixed over it. 1.0 would erase the gate completely;
  // the remainder is the silhouette. Settled by measurement, not taste —
  // see the note on the contrast target in the commit.
  veil: 0.55,
  veilNear: 6,
  veilFar: 30,
};

// The scene's own ground fog (near 3 / far 16) is tuned to dissolve the grass
// into the horizon, so anything past ~16 units is flat fog colour — a torii
// placed at a believable distance would simply not exist. This material opts
// out of that fog and applies its own, over a much longer range and with a
// ceiling below 1, so distance washes the gate down to a silhouette instead of
// deleting it. Fogging toward the sky's own colour in that direction (the same
// trick the ground plane uses) keeps it from reading as a grey cut-out.
function toriiMaterial() {
  const m = new THREE.MeshStandardMaterial({
    color: TORII.color,
    roughness: 0.92,
    metalness: 0.0,
    fog: false,
  });
  // Built outside onBeforeCompile and kept, so the group can hand them back for
  // tuning — the veil is a look value that only measurement can settle, and
  // re-editing the file per trial is a slow way to find it.
  const uniforms = {
    uSunDir: { value: new THREE.Vector3(...CONFIG.sun.position).normalize() },
    uSunGlow: { value: new THREE.Color(CONFIG.colors.sunGlow) },
    uVeilColor: { value: new THREE.Color(TORII.veilColor) },
    uVeil: { value: TORII.veil },
    uVeilRange: { value: new THREE.Vector2(TORII.veilNear, TORII.veilFar) },
  };
  m.userData.uniforms = uniforms;
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, uniforms);
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\n varying vec3 vTWorld;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\n vTWorld = ( modelMatrix * vec4( position, 1.0 ) ).xyz;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', `#include <common>
        uniform vec3 uSunDir; uniform vec3 uSunGlow; uniform vec3 uVeilColor;
        uniform float uVeil; uniform vec2 uVeilRange;
        varying vec3 vTWorld;` + SKY_GLOW_GLSL)
      .replace('#include <opaque_fragment>', `#include <opaque_fragment>
        vec3 vdir = normalize( vTWorld - cameraPosition );
        vec3 veilCol = uVeilColor + skyGlow( vdir, uSunDir, uSunGlow, exp( -vdir.y * vdir.y * 5.0 ) );
        float d = distance( vTWorld, cameraPosition );
        float veil = smoothstep( uVeilRange.x, uVeilRange.y, d ) * uVeil;
        gl_FragColor.rgb = mix( gl_FragColor.rgb, veilCol, veil );`);
  };
  return m;
}

// Where on the ground does the gate have to stand to appear at `ndcX`?
// Screen x is monotonic in the lateral offset at a fixed depth, so bisect on it
// rather than deriving it — the closed form has to account for the camera's yaw
// and pitch as well as the aspect, and this is exact to 40 halvings either way.
function solveGroundPlacement(camera, ndcX, distance, sampleHeight) {
  camera.updateMatrixWorld(true);
  const forward = new THREE.Vector3();
  camera.getWorldDirection(forward);
  forward.y = 0; forward.normalize();
  const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 1, 0)).normalize();
  const center = camera.position.clone().addScaledVector(forward, distance);
  center.y = 0;
  const probe = new THREE.Vector3();
  const xAt = (lateral) => {
    probe.copy(center).addScaledVector(right, lateral);
    probe.y = sampleHeight;
    return probe.project(camera).x;
  };
  let lo = -120, hi = 120;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    if (xAt(mid) < ndcX) lo = mid; else hi = mid;
  }
  return center.clone().addScaledVector(right, (lo + hi) / 2);
}

export function createTorii(camera) {
  const g = new THREE.Group();
  const mat = toriiMaterial();
  const { height, span, postRadius: r, postTaper } = TORII;

  // Hashira: the two uprights. Splayed outward at the foot by ~2°, which is
  // what stops a torii looking like a table.
  const postH = height * 0.88;
  for (const side of [-1, 1]) {
    const post = new THREE.Mesh(
      new THREE.CylinderGeometry(r * postTaper, r, postH, 10), mat);
    post.position.set(side * span / 2, postH / 2, 0);
    post.rotation.z = -side * 0.035;
    g.add(post);
  }

  // Kasagi: the top lintel, curving up at both ends. A shallow arc of short
  // boxes rather than a swept curve — at this distance only the outline reads.
  // Two things matter and both were wrong on the first pass: the segments must
  // overlap enough that rotating them cannot open gaps (an under-lapped arc
  // renders as a row of separate dashes), and the curve has to stay shallow, or
  // the ends kick up hard enough to read as a broken beam rather than a sweep.
  const kasagiW = span * 1.36;
  const segs = 13;
  const kasagiT = r * 0.66;
  for (let i = 0; i < segs; i++) {
    const u = (i + 0.5) / segs - 0.5;                    // -0.5 … +0.5
    const k = Math.abs(u) * 2;                           // 0 at centre, ~0.92 at the ends
    const seg = new THREE.Mesh(
      // 1.22× the spacing: covers the foreshortening from the tilt plus the
      // beam's own thickness swinging through it.
      new THREE.BoxGeometry(kasagiW / segs * 1.22, kasagiT, r * 1.5), mat);
    seg.position.set(u * kasagiW, height + Math.pow(k, 2.4) * 0.22, 0);
    seg.rotation.z = -Math.sign(u) * Math.pow(k, 1.8) * 0.20;
    g.add(seg);
  }

  // Shimaki: the straight beam packed under the kasagi's curve.
  const shimakiY = height - kasagiT * 0.9;
  const shimaki = new THREE.Mesh(
    new THREE.BoxGeometry(kasagiW * 0.93, r * 0.44, r * 1.28), mat);
  shimaki.position.set(0, shimakiY, 0);
  g.add(shimaki);

  // Nuki: the lower tie-beam. It sits about a sixth of the way down from the
  // top of the posts — derived from postH rather than from total height, so
  // the gap above it stays right if the proportions are ever retuned. It is
  // also narrower than the kasagi and pierces the posts rather than resting
  // on them, which is what distinguishes a myojin torii from a goalpost.
  const nukiY = postH * 0.86;
  const nuki = new THREE.Mesh(
    new THREE.BoxGeometry(span * 1.16, r * 0.50, r * 0.95), mat);
  nuki.position.set(0, nukiY, 0);
  g.add(nuki);

  // Gakuzuka: the short central strut filling the gap between nuki and shimaki.
  const gap = shimakiY - r * 0.22 - (nukiY + r * 0.25);
  const strut = new THREE.Mesh(new THREE.BoxGeometry(r * 0.75, gap, r * 0.7), mat);
  strut.position.set(0, nukiY + r * 0.25 + gap / 2, 0);
  g.add(strut);

  for (const m of g.children) {
    // No shadows: it stands far outside the sun's 60-unit shadow frustum, so
    // casting would cost a second pass over the geometry for nothing.
    m.castShadow = false;
    m.receiveShadow = false;
  }

  const framing = camera.aspect < TORII.portrait.aspectBelow ? TORII.portrait : TORII;
  g.position.copy(solveGroundPlacement(camera, framing.ndcX, framing.distance, height * 0.5));
  g.rotation.y = TORII.rotationY;
  g.userData.uniforms = mat.userData.uniforms;
  g.userData.dispose = () => {
    for (const m of g.children) m.geometry.dispose();
    mat.dispose();
  };
  return g;
}
