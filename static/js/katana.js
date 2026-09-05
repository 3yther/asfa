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

// Mokko-gata guard: a four-lobed plate with the blade slot and a pair of
// hitsu-ana openings, extruded with a small bevel so the rim catches light.
function tsubaGeometry() {
  const shape = new THREE.Shape();
  const R = 0.148, n = 72;
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * Math.PI * 2;
    const r = R * (0.9 + 0.10 * Math.cos(a * 4));
    const x = Math.cos(a) * r, y = Math.sin(a) * r;
    if (i === 0) shape.moveTo(x, y); else shape.lineTo(x, y);
  }
  // Shape x is the blade's depth axis after the rotation below, so the slot
  // is long in x and thin in y.
  const slot = new THREE.Path(); slot.absellipse(0, 0, 0.056, 0.015, 0, Math.PI * 2, false, 0);
  const kozuka = new THREE.Path(); kozuka.absellipse(0.0, 0.082, 0.020, 0.030, 0, Math.PI * 2, false, 0);
  const kogai = new THREE.Path(); kogai.absellipse(0.0, -0.082, 0.014, 0.028, 0, Math.PI * 2, false, 0);
  shape.holes.push(slot, kozuka, kogai);
  const g = new THREE.ExtrudeGeometry(shape, {
    depth: 0.014, bevelEnabled: true, bevelThickness: 0.003, bevelSize: 0.003, bevelSegments: 2, curveSegments: 24,
  });
  g.rotateX(Math.PI / 2);
  g.translate(0, 0.007, 0);
  return g;
}

// ── Mei: the maker's mark ────────────────────────────────────────────────────
// "AS", cut into the blade beside the bo-hi. A real mei is chiselled into the
// nakago, under the wrap where nobody would ever see it; this sits on the blade
// itself, which is the one liberty taken — placed spine-side of the groove,
// where a horimono would go, rather than out on the polished ji.
//
// Drawn to a canvas rather than built as an SDF: two glyphs at ~5 px on screen
// do not justify hand-rolling letterforms, and the font's own curves read
// better at that size than anything a distance field would give.
const MEI = {
  text: ['A', 'S'],
  // Extent on the blade in (along, section) space. `section` 0 is the mune and
  // 1 the cutting edge, so this straddles the bo-hi channel at 0.14…0.34.
  along: [0.190, 0.256],
  section: [0.07, 0.39],
  // The mark covers ~9×18 screen px, so the 48×96 texture is minified ~5× and
  // the mip chain averages each stroke down with the ground around it: measured
  // peak coverage lands near 0.16, not 1.0. A depth of 0.42 therefore darkened
  // the steel by 6.5% at its strongest, which is not "subtle", it is invisible.
  // Depth is scaled for that averaging and the result clamped, so a display
  // where the blade is larger (and coverage closer to 1) cannot drive the cut
  // to black.
  depth: 1.15,
  minBrightness: 0.62,   // floor on the cut, so the glyph never becomes a hole
  lip: 0.34,             // brightness of the lit edge on the light-facing side
};

let _meiTexture = null;
function meiTexture() {
  if (_meiTexture) return _meiTexture;
  // Tall and narrow: the texture's height runs down the blade, its width across.
  // Kept small deliberately — a 256px canvas would only be minified harder and
  // average the strokes away faster.
  const W = 48, H = 96;
  const c = document.createElement('canvas');
  c.width = W; c.height = H;
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#fff';
  ctx.strokeStyle = '#fff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  // A serif face reads as cut-with-a-chisel; the sans in the rest of the UI
  // reads as printed. Filled *and* stroked: at this size a hairline serif loses
  // its thin strokes to the mip chain entirely, and only the stems survive.
  ctx.font = `700 ${Math.round(H * 0.44)}px Georgia, "Times New Roman", serif`;
  ctx.lineWidth = 2.0;
  ctx.lineJoin = 'round';
  MEI.text.forEach((ch, i) => {
    const y = H * (0.26 + i * 0.47);
    ctx.strokeText(ch, W / 2, y);
    ctx.fillText(ch, W / 2, y);
  });
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
  // No mip chain. The mark is ~11×21 px on screen against a 48×96 source, so
  // trilinear sampling lands several levels down and averages each stroke into
  // its background — which is what turned the glyphs into a grey smudge rather
  // than letters. Plain bilinear off level 0 keeps the strokes. Normally that
  // trade buys shimmer, but neither this camera nor this sword ever moves, so
  // there is nothing for it to shimmer against.
  tex.generateMipmaps = false;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  // Three flips textures on upload by default, so v = 0 would sample the bottom
  // of the canvas. The sword is planted point-down, which makes vAlong = 0 the
  // top of the screen — with the flip left on, the mark rendered "S" over "A".
  tex.flipY = false;
  _meiTexture = tex;
  return tex;
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

  const uniforms = {
    uRim: { value: new THREE.Color(rimColor) },
    uMei: { value: meiTexture() },
    uMeiAlong: { value: new THREE.Vector2(...MEI.along) },
    uMeiSection: { value: new THREE.Vector2(...MEI.section) },
    uMeiDepth: { value: MEI.depth },
    uMeiFloor: { value: MEI.minBrightness },
    uMeiLip: { value: MEI.lip },
  };
  // Kept on the material so the mark's depth can be measured by differencing a
  // frame against the same frame with uMeiDepth at 0 — at ~5 px the mei is far
  // too small to judge by eye, and a diff says exactly which pixels it moved.
  m.userData.uniforms = uniforms;

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
        uniform sampler2D uMei;
        uniform vec2 uMeiAlong;
        uniform vec2 uMeiSection;
        uniform float uMeiDepth;
        uniform float uMeiFloor;
        uniform float uMeiLip;
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
        // Bo-hi: a fuller groove along the spine side, shaded as a channel with
        // a lit lip on the edge side. Runs from just past the habaki to the
        // start of the kissaki.
        float run = smoothstep( 0.04, 0.07, vAlong ) * ( 1.0 - smoothstep( 0.84, 0.88, vAlong ) );
        float groove = smoothstep( 0.14, 0.18, vSection ) * ( 1.0 - smoothstep( 0.30, 0.34, vSection ) ) * run;
        float lip = smoothstep( 0.32, 0.34, vSection ) * ( 1.0 - smoothstep( 0.36, 0.40, vSection ) ) * run;
        outgoingLight *= 1.0 - 0.32 * groove + 0.22 * lip;
        // Mei. Sampled in blade space, so it stays put under the taper and the
        // sori without needing its own UV set. Cut, not raised: the glyph
        // darkens the steel, and a one-texel offset toward the light picks out
        // a bright lip on the far wall of the cut — the shading that makes an
        // engraving read as engraved rather than as printed ink.
        vec2 meiUv = vec2(
          ( vSection - uMeiSection.x ) / ( uMeiSection.y - uMeiSection.x ),
          ( vAlong  - uMeiAlong.x  ) / ( uMeiAlong.y  - uMeiAlong.x  ) );
        if ( meiUv.x > 0.0 && meiUv.x < 1.0 && meiUv.y > 0.0 && meiUv.y < 1.0 ) {
          float ink = texture2D( uMei, meiUv ).a;
          float shifted = texture2D( uMei, meiUv + vec2( 0.05, 0.03 ) ).a;
          outgoingLight *= max( 1.0 - uMeiDepth * ink, uMeiFloor );
          outgoingLight *= 1.0 + uMeiLip * max( shifted - ink, 0.0 );
        }
        // Yokote: the crease where the point's polish meets the body.
        outgoingLight *= 1.0 + 0.10 * smoothstep( 0.892, 0.900, vAlong ) * ( 1.0 - smoothstep( 0.905, 0.925, vAlong ) );
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
        // Same (ray skin) shows in the diamonds: pale nodes on a dark ground.
        vec2 g = fract( vWrapUv * vec2( 34.0, 52.0 ) ) - 0.5;
        float node = 1.0 - smoothstep( 0.16, 0.30, length( g ) );
        vec3 same = mix( vec3( 0.10, 0.10, 0.09 ), vec3( 0.62, 0.60, 0.55 ), node );
        diffuseColor.rgb = mix( same, vec3( 0.95, 0.95, 0.91 ), silk );
        // Cord edges read as recessed where they cross.
        diffuseColor.rgb *= 1.0 - 0.18 * smoothstep( 0.30, 0.42, lattice ) * silk;`)
      .replace('#include <opaque_fragment>', `
        float ndv = max( dot( normalize( normal ), normalize( vViewPosition ) ), 0.0 );
        outgoingLight += uRim * pow( 1.0 - ndv, 3.5 ) * 0.25;
        #include <opaque_fragment>`);
  };

  const blade = new THREE.Mesh(buildBlade(), steel);
  group.add(blade);

  const brass = new THREE.MeshPhysicalMaterial({
    color: 0xb08a48, metalness: 1.0, roughness: 0.38, envMapIntensity: 2.2,
  });
  const gold = new THREE.MeshPhysicalMaterial({
    color: 0xd4a94a, metalness: 1.0, roughness: 0.3, envMapIntensity: 2.6,
  });

  // Habaki: the brass collar, two-stepped, snug on the blade.
  const habaki = new THREE.Mesh(new THREE.CylinderGeometry(0.046, 0.052, 0.042, 24), brass);
  habaki.position.y = -0.07;
  group.add(habaki);
  const habakiStep = new THREE.Mesh(new THREE.CylinderGeometry(0.052, 0.054, 0.012, 24), brass);
  habakiStep.position.y = -0.097;
  group.add(habakiStep);

  // Seppa: thin washers either side of the guard.
  for (const y of [-0.108, -0.132]) {
    const seppa = new THREE.Mesh(new THREE.CylinderGeometry(0.066, 0.066, 0.005, 32), brass);
    seppa.position.y = y;
    group.add(seppa);
  }

  const tsuba = new THREE.Mesh(tsubaGeometry(), iron);
  tsuba.position.y = -0.12;
  group.add(tsuba);

  // Fuchi: the collar where the wrap meets the guard.
  const fuchi = new THREE.Mesh(new THREE.CylinderGeometry(0.047, 0.045, 0.03, 24), iron);
  fuchi.position.y = -0.152;
  group.add(fuchi);

  const tsuka = new THREE.Mesh(
    new THREE.CylinderGeometry(HANDLE.radius, HANDLE.radius * 1.1, HANDLE.length, 24),
    wrap
  );
  tsuka.position.y = -0.167 - HANDLE.length / 2;
  group.add(tsuka);

  // Menuki: the small gold ornament under the wrap, a third of the way down.
  const menuki = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12), gold);
  menuki.scale.set(0.010, 0.032, 0.007);
  menuki.position.set(0, -0.167 - HANDLE.length * 0.36, HANDLE.radius + 0.002);
  menuki.rotation.z = 0.25;
  group.add(menuki);

  const kashira = new THREE.Mesh(new THREE.CylinderGeometry(0.047, 0.041, 0.045, 24), iron);
  kashira.position.y = -0.167 - HANDLE.length - 0.012;
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
