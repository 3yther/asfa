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

  const pos = [];
  const idx = [];

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
      for (const c of [SECTION[f], SECTION[(f + 1) % segs]]) {
        pos.push(
          spineX + c[0] * w * outX,
          spineY + c[0] * w * outY,
          c[1] * tk
        );
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
  g.setIndex(idx);
  g.computeVertexNormals();
  g.computeBoundingBox();
  return g;
}

export function createKatana() {
  const group = new THREE.Group();

  const steel = new THREE.MeshStandardMaterial({
    color: 0xd7dee2, metalness: 1.0, roughness: 0.17, envMapIntensity: 1.0,
  });
  const iron = new THREE.MeshStandardMaterial({
    color: 0x2a2f33, metalness: 0.85, roughness: 0.52,
  });
  const wrap = new THREE.MeshStandardMaterial({
    color: 0x14181b, metalness: 0.0, roughness: 0.88,
  });

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
