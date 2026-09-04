import * as THREE from 'three';
import { SKY_GLOW_GLSL } from './samurai-theme.js';

export const GRASS = {
  count: 32000,
  radius: 15,            // tighter disc = the dense near-field wall of the reference
  height: 0.82,          // base; per-instance 0.55×–1.35× of this
  stemWidth: 0.014,
  plumeWidth: 0.08,
  plumeStart: 0.62,      // susuki heads are long — the top ~40% of the stalk
  lean: [0.22, 0.52],    // baked, wind-ward tilt in radians, per instance
  lensClearance: 1.1,
  wind: { dir: [0.82, 0.57], speed: 1.15, freq: 0.55, strength: 0.30, gustFreq: 0.13 },
  colors: { base: 0x7d837e, mid: 0xc9cdc6, tip: 0xf0f2ee, glow: 0xe9f1ef },
};

// Wind is applied in world space, after the instance transform: bending in
// local space would rotate the gust with each blade's own Y rotation and the
// field would ripple in every direction at once instead of one.
const WIND_VERTEX = /* glsl */`
  vec4 mvPosition = vec4( transformed, 1.0 );
  #ifdef USE_INSTANCING
    mvPosition = instanceMatrix * mvPosition;
  #endif

  vec4 worldPos = modelMatrix * mvPosition;

  float t = uTime * uWindSpeed;
  float along = dot( worldPos.xz, uWindDir );
  // A travelling wave for the blade-scale ripple, plus a slower, broader gust
  // so the field surges in patches rather than sloshing as one sheet.
  float wave = sin( along * uWindFreq + t + aPhase );
  float gust = sin( along * uGustFreq + t * 0.35 ) * 0.5 + 0.5;

  // Squared height weighting anchors the blade at its base and lets the tip
  // travel — a linear weight makes the whole blade slide sideways.
  float bend = uWindStrength * ( 0.30 + 0.70 * gust ) * wave * vH * vH;
  worldPos.xz += uWindDir * bend;
  worldPos.y  -= abs( bend ) * 0.22;

  // Handed to the fragment stage so the plume shimmer is phase-locked to the
  // same motion the eye is already tracking.
  vWave = wave * ( 0.30 + 0.70 * gust );
  vWorldPos = worldPos.xyz;

  mvPosition = viewMatrix * worldPos;
  gl_Position = projectionMatrix * mvPosition;
`;

// A susuki stalk: a thin, barely tapering stem for four fifths of its height,
// then a feathered head. The head is real geometry (so it silhouettes and
// catches backlight) and the feathering is done with alpha in the fragment.
const STATIONS = [0, 0.3, 0.5, 0.62, 0.72, 0.82, 0.91, 1.0];

function halfWidth(v) {
  const { stemWidth, plumeWidth, plumeStart } = GRASS;
  const stem = stemWidth * 0.5 * (1 - 0.45 * Math.min(v / plumeStart, 1));
  if (v <= plumeStart) return stem;
  const t = (v - plumeStart) / (1 - plumeStart);
  // Teardrop: widest a third of the way up the head, then a long taper.
  const plume = plumeWidth * 0.5 * Math.pow(Math.sin(Math.pow(t, 0.62) * Math.PI), 0.7);
  return Math.max(stem * (1 - t), plume);
}

// Built as two pieces that share every instance attribute. The stem carries
// most of the pixels and is fully opaque, so the GPU can reject hidden
// fragments early; only the head, which needs alpha for its bristles, pays
// for discard. One alpha-tested draw for the whole blade measured at roughly
// double the cost, because near blades overlap several deep.
// The head is two strips crossed at 90°: one flat card reads as paper from
// any angle the wind turns it to; the cross gives it a body.
function bladeGeometry(stations, cross = false) {
  const pos = [], hs = [], side = [], idx = [];
  const planes = cross ? 2 : 1;
  for (let k = 0; k < planes; k++) {
    const base = pos.length / 3;
    for (const v of stations) {
      const w = halfWidth(v);
      if (k === 0) pos.push(-w, v, 0, w, v, 0);
      else pos.push(0, v, -w, 0, v, w);
      hs.push(v, v);
      side.push(-1, 1);
    }
    for (let i = 0; i < stations.length - 1; i++) {
      const a = base + i * 2, b = a + 1, c = a + 2, d = a + 3;
      idx.push(a, c, b, b, c, d);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('aHeight', new THREE.Float32BufferAttribute(hs, 1));
  g.setAttribute('aSide', new THREE.Float32BufferAttribute(side, 1));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

export function createGrassField(sun, skyGlow, cameraPos = new THREE.Vector3(0, 1.55, 4.8)) {
  const { count, radius, height, colors, plumeStart, lean } = GRASS;
  const split = STATIONS.indexOf(plumeStart);
  const stemGeo = bladeGeometry(STATIONS.slice(0, split + 1));
  const headGeo = bladeGeometry(STATIONS.slice(split), true);

  // Instances are laid out once and sorted front-to-back: the camera never
  // moves, so the opaque stem draw gets early-z rejection for free.
  const placements = [];
  const clear2 = GRASS.lensClearance ** 2;
  for (let i = 0; i < count; i++) {
    // sqrt keeps the disc evenly covered; a raw uniform radius clumps at the centre
    let x, z;
    do {
      const r = Math.sqrt(Math.random()) * radius;
      const a = Math.random() * Math.PI * 2;
      x = Math.cos(a) * r; z = Math.sin(a) * r;
      // The camera sits inside the field; nothing may grow on the lens.
    } while ((x - cameraPos.x) ** 2 + (z - cameraPos.z) ** 2 < clear2);
    placements.push({
      x, z, rot: Math.random() * Math.PI,
      // A standing tilt down-wind, baked into the placement. The wind shader
      // adds the flutter; this is the set of the field under a steady blow.
      lean: lean[0] + Math.random() * (lean[1] - lean[0]),
      // Wide height spread is what separates a wild field from a lawn.
      h: height * (0.55 + Math.random() * 0.8),
      w: 0.8 + Math.random() * 0.5,
      phase: Math.random() * Math.PI * 2,
      tint: Math.random(),
      d: (x - cameraPos.x) ** 2 + (z - cameraPos.z) ** 2,
    });
  }
  placements.sort((p, q) => p.d - q.d);

  const phase = new Float32Array(count);
  const tint = new Float32Array(count);
  for (let i = 0; i < count; i++) { phase[i] = placements[i].phase; tint[i] = placements[i].tint; }
  const phaseAttr = new THREE.InstancedBufferAttribute(phase, 1);
  const tintAttr = new THREE.InstancedBufferAttribute(tint, 1);
  for (const g of [stemGeo, headGeo]) {
    g.setAttribute('aPhase', phaseAttr);
    g.setAttribute('aTint', tintAttr);
  }

  const baseMaterial = () => new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 0.82,
    metalness: 0.0,
    // The sky IBL alone would wash the heads to white; the backlight terms in
    // the fragment stage are what should carry the brightness.
    envMapIntensity: 0.35,
    side: THREE.DoubleSide,
  });
  const stemMaterial = baseMaterial();
  const headMaterial = baseMaterial();
  // Feathered plume edges without a transparent sort: MSAA is on, so coverage
  // does the blending and the heads stay a single draw call.
  headMaterial.alphaToCoverage = true;
  headMaterial.alphaTest = 0.45;

  const uniforms = {
    uTime:        { value: 0 },
    uWindDir:     { value: new THREE.Vector2(...GRASS.wind.dir).normalize() },
    uWindSpeed:   { value: GRASS.wind.speed },
    uWindFreq:    { value: GRASS.wind.freq },
    uWindStrength:{ value: GRASS.wind.strength },
    uGustFreq:    { value: GRASS.wind.gustFreq },
    uSunDir:      { value: sun.position.clone().normalize() },
    uSunColor:    { value: sun.color.clone() },
    uBase:        { value: new THREE.Color(colors.base) },
    uMid:         { value: new THREE.Color(colors.mid) },
    uTip:         { value: new THREE.Color(colors.tip) },
    uGlow:        { value: new THREE.Color(colors.glow) },
    uSunGlow:     { value: new THREE.Color(skyGlow) },
  };

  const onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);

    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>
        attribute float aHeight;
        attribute float aSide;
        attribute float aPhase;
        attribute float aTint;
        uniform float uTime;
        uniform vec2  uWindDir;
        uniform float uWindSpeed;
        uniform float uWindFreq;
        uniform float uWindStrength;
        uniform float uGustFreq;
        varying float vH;
        varying float vSide;
        varying float vTint;
        varying float vWave;
        varying vec3  vWorldPos;`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        vH = aHeight;
        vSide = aSide;
        vTint = aTint;`)
      .replace('#include <project_vertex>', WIND_VERTEX);

    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
        uniform vec3  uSunDir;
        uniform vec3  uSunColor;
        uniform vec3  uBase;
        uniform vec3  uMid;
        uniform vec3  uTip;
        uniform vec3  uGlow;
        uniform vec3  uSunGlow;
        varying float vH;
        varying float vSide;
        varying float vTint;
        varying float vWave;
        varying vec3  vWorldPos;
        ` + SKY_GLOW_GLSL)
      .replace('#include <color_fragment>', `#include <color_fragment>
        // Warm taupe at the shadowed base rising to silver-cream at the head.
        vec3 stalk = mix( uBase, uMid, smoothstep( 0.0, 0.55, vH ) );
        stalk = mix( stalk, uTip, smoothstep( 0.50, 1.0, vH ) );
        stalk *= mix( 0.80, 1.05, vTint );
        diffuseColor.rgb = stalk;

        // The head is cut into bristles rather than made uniformly translucent:
        // a spiky silhouette is what reads as a plume, four-level coverage
        // blending on a solid leaf shape just reads as a paler leaf.
        float plume = smoothstep( 0.78, 0.86, vH );
        float pt = clamp( ( vH - 0.80 ) / 0.20, 0.0, 1.0 );
        float u = vSide;
        // Barbs, not stripes: thin lines leaving the rachis at an angle, the
        // way florets sit on a plume, with most of the head left as gaps. A
        // filled silhouette reads as a leaf no matter how its edge is cut.
        float ua = abs( u );
        // Coarse on purpose: at this framing a head is 40–80 px, so finer
        // barbs average into a solid fill under 2× coverage sampling.
        float l1 = abs( fract( pt * 9.0 - ua * 2.6 + vTint * 5.0 ) - 0.5 ) * 2.0;
        float l2 = abs( fract( pt * 17.0 + ua * 1.4 - vTint * 8.0 ) - 0.5 ) * 2.0;
        // Near-binary on purpose: with two coverage samples, anything between
        // 0.3 and 0.7 alpha lands as a flat half-tone and the head fills in.
        float barb = max( 1.0 - smoothstep( 0.22, 0.30, l1 ), 0.9 * ( 1.0 - smoothstep( 0.12, 0.19, l2 ) ) );
        // Fluff: sparse specks between the barbs, not a wash.
        float fluff = smoothstep( 0.86, 0.95, abs( sin( u * 31.0 + pt * 19.0 + vTint * 9.0 ) ) ) * 0.9;
        // Ragged edge: the reach of the barbs varies along the head, so the
        // outline is never the clean teardrop the geometry has.
        float reach = 0.5 + 0.5 * abs( sin( pt * 5.0 + vTint * 7.0 ) * sin( pt * 11.0 - vTint * 3.0 ) );
        float feather = 1.0 - smoothstep( reach - 0.35, reach, ua );
        float core = smoothstep( 0.10, 0.0, ua );
        float head = max( max( barb, fluff ) * feather * ( 1.05 - 0.4 * pt ), core );
        diffuseColor.a = mix( 1.0, head, plume );
        // Far heads dissolve rather than stopping at the disc's edge.
        float dcam = length( cameraPosition - vWorldPos );
        diffuseColor.a *= mix( 1.0, 1.0 - smoothstep( 9.0, 14.5, dcam ), plume );
        // Grey in the body, white at the florets' ends: the reference plumes
        // are shadowed inside and lit only where they thin out.
        float edge = smoothstep( 0.2, 0.9, ua );
        diffuseColor.rgb *= mix( 0.62, 1.0, mix( 1.0, edge, plume ) );
        diffuseColor.rgb *= 1.0 - 0.30 * plume * smoothstep( 0.30, 0.0, ua );
        // Canopy occlusion: heads low in the mass sit in the shadow of the
        // ones above them. This is what gives a wall of white plumes depth.
        diffuseColor.rgb *= mix( 0.42, 1.0, smoothstep( 0.15, 1.05, vWorldPos.y ) );
        diffuseColor.rgb *= 0.93 + 0.07 * sin( vH * 90.0 + vTint * 40.0 );`)
      .replace('#include <opaque_fragment>', `
        vec3 V = normalize( cameraPosition - vWorldPos );

        // Translucency: a stalk between the eye and the sun glows instead of
        // going dark. Thin tips pass more light than the base, and the
        // per-stalk variation stands in for the self-shadowing of a dense sward.
        float back = pow( max( dot( V, -uSunDir ), 0.0 ), 2.0 );
        float passes = ( 0.18 + 0.82 * vH ) * mix( 0.45, 1.0, vTint );
        outgoingLight += uGlow * uSunColor * back * passes * 0.75;

        // Plume shimmer, driven by the wind phase so the flash and the sway
        // are the same motion.
        float shimmer = pow( max( vWave, 0.0 ), 3.0 ) * smoothstep( 0.66, 1.0, vH );
        outgoingLight += uGlow * shimmer * ( 0.25 + 0.75 * back ) * 0.9;
        // Floret ends catch the sky: the bright fringe on every head.
        outgoingLight += uGlow * edge * plume * ( 0.35 + 0.65 * back ) * 0.55;

        // Distant stalks lose contrast before the fog lifts them.
        float dist = length( cameraPosition - vWorldPos );
        float fade = smoothstep( 5.0, 22.0, dist );
        float lum = dot( outgoingLight, vec3( 0.2126, 0.7152, 0.0722 ) );
        outgoingLight = mix( outgoingLight, vec3( lum ), fade * 0.55 );

        #include <opaque_fragment>`)
      .replace('#include <fog_fragment>', `
        #ifdef USE_FOG
          vec3 fdir = normalize( vWorldPos - cameraPosition );
          float fband = exp( -fdir.y * fdir.y * 7.0 );
          vec3 fcol = fogColor + skyGlow( fdir, uSunDir, uSunGlow, fband );
          float ff = smoothstep( fogNear, fogFar, vFogDepth );
          gl_FragColor.rgb = mix( gl_FragColor.rgb, fcol, ff );
        #endif`);
  };

  stemMaterial.onBeforeCompile = onBeforeCompile;
  headMaterial.onBeforeCompile = onBeforeCompile;

  const stem = new THREE.InstancedMesh(stemGeo, stemMaterial, count);
  const head = new THREE.InstancedMesh(headGeo, headMaterial, count);
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const p = new THREE.Vector3();
  const sc = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  const wd = new THREE.Vector2(...GRASS.wind.dir).normalize();
  // Axis perpendicular to the wind in the ground plane: rotating "up" about it
  // tips the stalk down-wind.
  const leanAxis = new THREE.Vector3(wd.y, 0, -wd.x);
  const qLean = new THREE.Quaternion();
  for (let i = 0; i < count; i++) {
    const pl = placements[i];
    p.set(pl.x, 0, pl.z);
    q.setFromAxisAngle(up, pl.rot);
    qLean.setFromAxisAngle(leanAxis, pl.lean);
    q.premultiply(qLean);
    sc.set(pl.w, pl.h, 1);
    m.compose(p, q, sc);
    stem.setMatrixAt(i, m);
  }
  stem.instanceMatrix.needsUpdate = true;
  // Same placements, one upload: the head reuses the stem's matrix buffer.
  head.instanceMatrix = stem.instanceMatrix;

  const mesh = new THREE.Group();
  for (const part of [stem, head]) {
    part.castShadow = false;
    part.receiveShadow = true;
    part.frustumCulled = false;
    mesh.add(part);
  }

  mesh.userData.update = (dt, t) => { uniforms.uTime.value = t; };
  mesh.userData.setSun = (light) => {
    uniforms.uSunDir.value.copy(light.position).normalize();
    uniforms.uSunColor.value.copy(light.color);
  };
  return mesh;
}
