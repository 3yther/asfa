import * as THREE from 'three';

export const MIST = {
  count: 340, box: 46, yRange: [0.05, 1.9],
  size: [2.6, 6.4], drift: 0.28, opacity: 0.115, color: 0xc9d3c8,
};
export const DUST = {
  count: 620, box: 34, yRange: [0.15, 2.8],
  size: [0.05, 0.17], drift: 0.95, opacity: 0.42, color: 0xdfe6da,
};

// Generated rather than loaded: the page's CSP is connect-src 'self', so an
// external sprite would be blocked, and a radial falloff is two lines anyway.
function softSprite() {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0.0, 'rgba(255,255,255,1)');
  g.addColorStop(0.45, 'rgba(255,255,255,0.34)');
  g.addColorStop(1.0, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const VERT = /* glsl */`
attribute float aSize;
attribute float aPhase;
uniform float uTime;
uniform vec2  uWindDir;
uniform float uDrift;
uniform float uBox;
uniform float uBob;
varying float vFade;

void main() {
  vec3 p = position;
  // Wrap inside the box so a finite set of particles drifts forever; the +uBox
  // keeps mod() away from negative operands.
  float travel = uTime * uDrift;
  p.x = mod( p.x + uWindDir.x * travel + uBox * 1.5, uBox ) - uBox * 0.5;
  p.z = mod( p.z + uWindDir.y * travel + uBox * 1.5, uBox ) - uBox * 0.5;
  p.y += sin( uTime * 0.34 + aPhase ) * uBob;

  vec4 mv = modelViewMatrix * vec4( p, 1.0 );
  float dist = -mv.z;

  // Fade at both ends: distant particles would otherwise pile up into a haze
  // wall, and near ones smear across the whole frame as they clip the camera.
  vFade = smoothstep( 0.6, 3.0, dist ) * ( 1.0 - smoothstep( uBox * 0.30, uBox * 0.52, dist ) );

  gl_PointSize = aSize * ( 300.0 / max( dist, 0.001 ) );
  gl_Position = projectionMatrix * mv;
}`;

const FRAG = /* glsl */`
uniform sampler2D uMap;
uniform vec3  uColor;
uniform float uOpacity;
uniform vec3  uFogColor;
uniform float uFogNear;
uniform float uFogFar;
varying float vFade;

void main() {
  float a = texture2D( uMap, gl_PointCoord ).a * uOpacity * vFade;
  if ( a < 0.004 ) discard;
  gl_FragColor = vec4( uColor, a );

  #ifdef USE_FOG
    float depth = gl_FragCoord.z / gl_FragCoord.w;
    float f = smoothstep( uFogNear, uFogFar, depth );
    gl_FragColor.rgb = mix( gl_FragColor.rgb, uFogColor, f );
  #endif
}`;

function buildLayer(cfg, sprite, fog) {
  const { count, box, yRange, size } = cfg;
  const pos = new Float32Array(count * 3);
  const sz = new Float32Array(count);
  const ph = new Float32Array(count);

  for (let i = 0; i < count; i++) {
    pos[i * 3]     = (Math.random() - 0.5) * box;
    pos[i * 3 + 1] = yRange[0] + Math.random() * (yRange[1] - yRange[0]);
    pos[i * 3 + 2] = (Math.random() - 0.5) * box;
    sz[i] = size[0] + Math.random() * (size[1] - size[0]);
    ph[i] = Math.random() * Math.PI * 2;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('aSize', new THREE.BufferAttribute(sz, 1));
  geo.setAttribute('aPhase', new THREE.BufferAttribute(ph, 1));

  const uniforms = {
    uTime:     { value: 0 },
    uWindDir:  { value: new THREE.Vector2(0.82, 0.57).normalize() },
    uDrift:    { value: cfg.drift },
    uBox:      { value: box },
    uBob:      { value: cfg === MIST ? 0.10 : 0.26 },
    uMap:      { value: sprite },
    uColor:    { value: new THREE.Color(cfg.color) },
    uOpacity:  { value: cfg.opacity },
    uFogColor: { value: new THREE.Color(fog.color) },
    uFogNear:  { value: fog.near },
    uFogFar:   { value: fog.far },
  };

  const mat = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: VERT,
    fragmentShader: FRAG,
    transparent: true,
    depthWrite: false,
    blending: THREE.NormalBlending,
    // Fog is applied by hand in FRAG with the uFog* uniforms below. Setting
    // fog:true would make the renderer write the built-in fogColor/fogNear/
    // fogFar uniforms, which this shader never declares.
    fog: false,
    defines: { USE_FOG: '' },
  });

  const pts = new THREE.Points(geo, mat);
  pts.frustumCulled = false;
  return { points: pts, uniforms };
}

export function createParticles(fog) {
  const sprite = softSprite();
  const mist = buildLayer(MIST, sprite, fog);
  const dust = buildLayer(DUST, sprite, fog);

  const group = new THREE.Group();
  group.add(mist.points, dust.points);

  group.userData.update = (dt, t) => {
    mist.uniforms.uTime.value = t;
    dust.uniforms.uTime.value = t;
  };
  group.userData.dispose = () => {
    sprite.dispose();
    for (const l of [mist, dust]) { l.points.geometry.dispose(); l.points.material.dispose(); }
  };
  return group;
}
