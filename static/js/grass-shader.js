import * as THREE from 'three';

export const GRASS = {
  count: 42000,
  radius: 24,
  height: [0.34, 0.78],
  width: 0.026,
  segments: 4,
  wind: { dir: [0.82, 0.57], speed: 1.15, freq: 0.55, strength: 0.30, gustFreq: 0.13 },
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

  mvPosition = viewMatrix * worldPos;
  gl_Position = projectionMatrix * mvPosition;
`;

function bladeGeometry() {
  const { segments, width } = GRASS;
  const pos = [], hs = [], idx = [];

  for (let i = 0; i <= segments; i++) {
    const v = i / segments;
    const w = width * 0.5 * Math.pow(1 - v, 0.65);
    pos.push(-w, v, 0, w, v, 0);
    hs.push(v, v);
  }
  for (let i = 0; i < segments; i++) {
    const a = i * 2, b = a + 1, c = a + 2, d = a + 3;
    idx.push(a, c, b, b, c, d);
  }

  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('aHeight', new THREE.Float32BufferAttribute(hs, 1));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

export function createGrassField() {
  const geo = bladeGeometry();
  const { count, radius, height } = GRASS;

  const phase = new Float32Array(count);
  const tint = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    phase[i] = Math.random() * Math.PI * 2;
    tint[i] = Math.random();
  }
  geo.setAttribute('aPhase', new THREE.InstancedBufferAttribute(phase, 1));
  geo.setAttribute('aTint', new THREE.InstancedBufferAttribute(tint, 1));

  const material = new THREE.MeshStandardMaterial({
    color: 0x67754e,
    roughness: 0.92,
    metalness: 0.0,
    side: THREE.DoubleSide,
  });

  const uniforms = {
    uTime:        { value: 0 },
    uWindDir:     { value: new THREE.Vector2(...GRASS.wind.dir).normalize() },
    uWindSpeed:   { value: GRASS.wind.speed },
    uWindFreq:    { value: GRASS.wind.freq },
    uWindStrength:{ value: GRASS.wind.strength },
    uGustFreq:    { value: GRASS.wind.gustFreq },
  };

  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);

    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>
        attribute float aHeight;
        attribute float aPhase;
        attribute float aTint;
        uniform float uTime;
        uniform vec2  uWindDir;
        uniform float uWindSpeed;
        uniform float uWindFreq;
        uniform float uWindStrength;
        uniform float uGustFreq;
        varying float vH;
        varying float vTint;`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        vH = aHeight;
        vTint = aTint;`)
      .replace('#include <project_vertex>', WIND_VERTEX);

    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
        varying float vH;
        varying float vTint;`)
      .replace('#include <color_fragment>', `#include <color_fragment>
        // Light is occluded near the base of a dense sward; without this the
        // field reads as a flat carpet rather than something with depth.
        diffuseColor.rgb *= mix( 0.34, 1.12, vH );
        diffuseColor.rgb *= mix( 0.86, 1.12, vTint );`);
  };

  const mesh = new THREE.InstancedMesh(geo, material, count);
  mesh.castShadow = false;
  mesh.receiveShadow = true;
  mesh.frustumCulled = false;

  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const p = new THREE.Vector3();
  const sc = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);

  for (let i = 0; i < count; i++) {
    // sqrt keeps the disc evenly covered; a raw uniform radius clumps at the centre
    const r = Math.sqrt(Math.random()) * radius;
    const a = Math.random() * Math.PI * 2;
    p.set(Math.cos(a) * r, 0, Math.sin(a) * r);

    q.setFromAxisAngle(up, Math.random() * Math.PI);
    const h = height[0] + Math.random() * (height[1] - height[0]);
    sc.set(0.75 + Math.random() * 0.5, h, 1);

    m.compose(p, q, sc);
    mesh.setMatrixAt(i, m);
  }
  mesh.instanceMatrix.needsUpdate = true;

  mesh.userData.update = (dt, t) => { uniforms.uTime.value = t; };
  return mesh;
}
