import * as THREE from 'three';

const BLADE = {
  length: 2.45,
  arc: 0.28,        // sori — total sweep angle of the spine, in radians
  depth: 0.105,     // edge-to-mune
  thickness: 0.030,
  stations: 96,
};

const HANDLE = { length: 0.82, radius: 0.040 };

// A katana cross-section (shinogi-zukuri): a sharp edge, a ridge line partway
// up each face, then the flat mune. Coordinates are (a, u) — `a` runs from the
// mune toward the cutting edge, `u` across the thickness.
const SECTION = [
  [ 0.55,  0.00],
  [ 0.02,  0.50],
  [-0.42,  0.30],
  [-0.48,  0.00],
  [-0.42, -0.30],
  [ 0.02, -0.50],
];
// Per-corner: how far across the blade (0 = mune, 1 = edge) and how much the
// corner belongs to the cutting edge. Drives the hamon and the rim light.
const SECTION_POS  = [1.0, 0.55, 0.10, 0.0, 0.10, 0.55];
const SECTION_EDGE = [1.0, 0.22, 0.0,  0.0, 0.0,  0.22];

// Gradual taper down the blade, then the kissaki collapsing to the point.
function taper(t) {
  const body = 1 - 0.14 * t;
  if (t < 0.9) return body;
  const k = (t - 0.9) / 0.1;
  return body * Math.sqrt(Math.max(0, 1 - k * k));
}

// Each cross-section corner is a crease, so its two adjoining faces get their
// own vertices. Sharing them would let computeVertexNormals average across the
// shinogi and round off the one line that reads as a katana.
function buildBlade() {
  const { length, arc, depth, thickness, stations } = BLADE;
  const R = length / Math.sin(arc);
  const segs = SECTION.length;

  const pos = [], uv = [], sec = [], edge = [], idx = [];

  for (let i = 0; i <= stations; i++) {
    const t = i / stations;
    const th = t * arc;
    const spineX = R * (1 - Math.cos(th));
    const spineY = R * Math.sin(th);
    const outX = -Math.cos(th);
    const outY = Math.sin(th);

    const s = taper(t);
    const w = depth * s;
    const tk = thickness * (1 - 0.10 * t) * (t < 0.9 ? 1 : s / taper(0.9));

    for (let f = 0; f < segs; f++) {
      for (const ci of [f, (f + 1) % segs]) {
        const c = SECTION[ci];
        pos.push(spineX + c[0] * w * outX, spineY + c[0] * w * outY, c[1] * tk);
        // u runs down the blade so the derived tangent — and with it the
        // anisotropic highlight — stretches along its length.
        uv.push(t, SECTION_POS[ci]);
        sec.push(SECTION_POS[ci]);
        edge.push(SECTION_EDGE[ci]);
      }
    }
  }

  const perStation = segs * 2;
  for (let i = 0; i < stations; i++) {
    for (let f = 0; f < segs; f++) {
      const a = i * perStation + f * 2;
      const b = a + 1;
      const c = a + perStation;
      const d = b + perStation;
      idx.push(a, c, b, b, c, d);
    }
  }

  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setAttribute('aSection', new THREE.Float32BufferAttribute(sec, 1));
  g.setAttribute('aEdge', new THREE.Float32BufferAttribute(edge, 1));
  g.setIndex(idx);
  g.computeVertexNormals();
  g.computeTangents();
  g.computeBoundingBox();
  return g;
}

function steelMaterial(rimColor) {
  const m = new THREE.MeshPhysicalMaterial({
    color: 0xcfdadf,
    metalness: 1.0,
    roughness: 0.2,
    envMapIntensity: 4.0,
    anisotropy: 0.85,
    anisotropyRotation: 0,
  });

  const uniforms = { uRim: { value: new THREE.Color(rimColor) } };

  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>
        attribute float aSection;
        attribute float aEdge;
        varying float vSection;
        varying float vEdge;
        varying float vAlong;`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        vSection = aSection;
        vEdge = aEdge;
        vAlong = uv.x;`);

    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
        uniform vec3 uRim;
        varying float vSection;
        varying float vEdge;
        varying float vAlong;`)
      // Hamon: the differentially hardened edge steel takes a finer polish
      // than the spine, so it is glossier on one side of a soft, wavy line.
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
        float hb = 0.56 + 0.05 * sin( vAlong * 41.0 ) + 0.025 * sin( vAlong * 97.0 + 1.7 );
        float hamon = smoothstep( hb - 0.08, hb + 0.08, vSection );
        roughnessFactor = mix( roughnessFactor * 1.7, roughnessFactor * 0.6, hamon );`)
      // Fresnel rim along the cutting edge — meant to be the brightest thing in
      // frame and to carry the bloom.
      .replace('#include <opaque_fragment>', `
        float ndv = max( dot( normalize( normal ), normalize( vViewPosition ) ), 0.0 );
        float fres = pow( 1.0 - ndv, 3.2 );
        outgoingLight += uRim * fres * ( 0.35 + 0.65 * vEdge ) * 4.2;
        // Polished steel under a bright overcast stays bright face-on too.
        outgoingLight += uRim * 0.12;
        outgoingLight *= 1.0 + 0.30 * hamon;
        #include <opaque_fragment>`);
  };
  return m;
}

// Lacquered wood and iron: dark for contrast, but with a specular roll-off so
// they read as objects rather than a silhouette cut from the frame.
function fittingsMaterial(rimColor) {
  const m = new THREE.MeshPhysicalMaterial({
    color: 0x1c1c1c, metalness: 0.9, roughness: 0.40, envMapIntensity: 0.9,
  });
  const uniforms = { uRim: { value: new THREE.Color(rimColor) } };
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
        uniform vec3 uRim;`)
      .replace('#include <opaque_fragment>', `
        float ndv = max( dot( normalize( normal ), normalize( vViewPosition ) ), 0.0 );
        outgoingLight += uRim * pow( 1.0 - ndv, 4.0 ) * 0.55;
        #include <opaque_fragment>`);
  };
  return m;
}

export function createKatana(rimColor = 0xeaf3f5) {
  const group = new THREE.Group();

  const steel = steelMaterial(rimColor);
  const iron = fittingsMaterial(rimColor);
  // Tsuka-ito: white silk wrapped in the diamond pattern, dark same showing
  // through the gaps. Drawn in the shader on the cylinder's UVs.
  const wrap = new THREE.MeshPhysicalMaterial({
    color: 0xffffff, metalness: 0.0, roughness: 0.62, clearcoat: 0.25, clearcoatRoughness: 0.5,
  });
  wrap.onBeforeCompile = (shader) => {
    shader.uniforms.uRim = { value: new THREE.Color(rimColor) };
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\n varying vec2 vWrapUv;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\n vWrapUv = uv;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n uniform vec3 uRim; varying vec2 vWrapUv;`)
      .replace('#include <color_fragment>', `#include <color_fragment>
        float a = fract( vWrapUv.x * 6.0 + vWrapUv.y * 9.0 );
        float b = fract( vWrapUv.x * 6.0 - vWrapUv.y * 9.0 );
        float lattice = min( abs( a - 0.5 ), abs( b - 0.5 ) ) * 2.0;
        float silk = 1.0 - smoothstep( 0.42, 0.56, lattice );
        diffuseColor.rgb = mix( vec3( 0.13, 0.13, 0.12 ), vec3( 0.95, 0.95, 0.91 ), silk );`)
      .replace('#include <opaque_fragment>', `
        float ndv = max( dot( normalize( normal ), normalize( vViewPosition ) ), 0.0 );
        outgoingLight += uRim * pow( 1.0 - ndv, 3.5 ) * 0.25;
        #include <opaque_fragment>`);
  };

  const blade = new THREE.Mesh(buildBlade(), steel);
  group.add(blade);

  const habaki = new THREE.Mesh(
    new THREE.CylinderGeometry(0.052, 0.055, 0.10, 20), iron
  );
  habaki.position.y = -0.05;
  group.add(habaki);

  const tsuba = new THREE.Mesh(
    new THREE.CylinderGeometry(0.155, 0.155, 0.018, 40), iron
  );
  tsuba.position.y = -0.115;
  group.add(tsuba);

  const tsuka = new THREE.Mesh(
    new THREE.CylinderGeometry(HANDLE.radius, HANDLE.radius * 1.12, HANDLE.length, 20),
    wrap
  );
  tsuka.position.y = -0.125 - HANDLE.length / 2;
  group.add(tsuka);

  const kashira = new THREE.Mesh(
    new THREE.CylinderGeometry(0.046, 0.040, 0.045, 20), iron
  );
  kashira.position.y = -0.125 - HANDLE.length - 0.012;
  group.add(kashira);

  for (const m of group.children) {
    m.castShadow = true;
    m.receiveShadow = true;
  }

  // Planted point-down, so flip and drop the tip below the ground line.
  group.rotation.z = Math.PI;
  group.position.set(0, BLADE.length - 0.34, 0);
  group.rotation.y = -0.42;

  return group;
}
