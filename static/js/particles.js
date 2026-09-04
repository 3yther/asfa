import * as THREE from 'three';
import { SKY_GLOW_GLSL, NOISE_GLSL } from './samurai-theme.js';

export const MIST = {
  count: 60, box: 46, yRange: [0.05, 1.9],
  size: [3.2, 8.5], drift: 0.22, opacity: 0.05, color: 0xb9c4c4,
};
// Sparse, large, soft flecks — seed fluff on the wind — readable one by one
// against the dark dome rather than a dust haze.
export const DUST = {
  count: 90, box: 32, yRange: [0.3, 3.0], spread: 6.0,
  size: [0.14, 0.55], drift: 0.5, opacity: 0.8, color: 0xf3f6f2,
};

// Low haze at three depths and speeds; cool and faint under the overcast.
export const MIST_BANDS = [
  { z: -2.5,  y: 0.55, height: 1.15, width: 60, speed: 0.045, scale: 0.9, opacity: 0.16 },
  { z: -7.5,  y: 0.85, height: 1.8,  width: 80, speed: 0.028, scale: 0.55, opacity: 0.22 },
  { z: -14.0, y: 1.25, height: 2.8,  width: 120, speed: 0.016, scale: 0.35, opacity: 0.30 },
];

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
attribute float aAlpha;
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
  // A slow per-particle pulse gives the opacity variation a still field lacks.
  float pulse = 0.55 + 0.45 * sin( uTime * 0.7 + aPhase * 3.1 );
  vFade = aAlpha * pulse
        * smoothstep( 0.6, 3.0, dist ) * ( 1.0 - smoothstep( uBox * 0.30, uBox * 0.52, dist ) );

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

// Approximate normal deviate — dust is biased toward the lit ground around the
// blade so the key light has something to be seen in.
function gauss() {
  return (Math.random() + Math.random() + Math.random() - 1.5) * 1.35;
}

function buildLayer(cfg, sprite, fog) {
  const { count, box, yRange, size } = cfg;
  const pos = new Float32Array(count * 3);
  const sz = new Float32Array(count);
  const ph = new Float32Array(count);
  const al = new Float32Array(count);

  for (let i = 0; i < count; i++) {
    if (cfg.spread) {
      pos[i * 3]     = THREE.MathUtils.clamp(gauss() * cfg.spread, -box / 2, box / 2);
      pos[i * 3 + 2] = THREE.MathUtils.clamp(gauss() * cfg.spread, -box / 2, box / 2);
    } else {
      pos[i * 3]     = (Math.random() - 0.5) * box;
      pos[i * 3 + 2] = (Math.random() - 0.5) * box;
    }
    pos[i * 3 + 1] = yRange[0] + Math.random() * (yRange[1] - yRange[0]);
    sz[i] = size[0] + Math.random() * (size[1] - size[0]);
    ph[i] = Math.random() * Math.PI * 2;
    al[i] = 0.25 + Math.random() * 0.75;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('aSize', new THREE.BufferAttribute(sz, 1));
  geo.setAttribute('aPhase', new THREE.BufferAttribute(ph, 1));
  geo.setAttribute('aAlpha', new THREE.BufferAttribute(al, 1));

  const uniforms = {
    uTime:     { value: 0 },
    uWindDir:  { value: new THREE.Vector2(0.82, 0.57).normalize() },
    uDrift:    { value: cfg.drift },
    uBox:      { value: box },
    uBob:      { value: cfg === MIST ? 0.10 : 0.22 },
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

const BAND_VERT = /* glsl */`
varying vec2 vUv;
varying vec3 vWorldPos;
void main() {
  vUv = uv;
  vWorldPos = ( modelMatrix * vec4( position, 1.0 ) ).xyz;
  gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
}`;

// Value-noise fbm scrolled with the wind. The band takes the same colour the
// sky dome shows behind it — haze included — so it never reads as a grey card.
const BAND_FRAG = SKY_GLOW_GLSL + NOISE_GLSL + /* glsl */`
uniform float uTime;
uniform float uSpeed;
uniform float uScale;
uniform float uOpacity;
uniform vec3  uFogColor;
uniform vec3  uSunDir;
uniform vec3  uSunGlow;
uniform vec2  uWindDir;
varying vec2 vUv;
varying vec3 vWorldPos;

void main() {
  vec2 p = vec2( vUv.x * 9.0, vUv.y * 2.2 ) * uScale;
  p.x += uTime * uSpeed * uWindDir.x * 6.0;
  p.y += uTime * uSpeed * 0.35;
  float n = fbm( p );
  n = smoothstep( 0.32, 0.78, n );
  // Bands sit on the ground and thin out upward.
  float vert = smoothstep( 0.0, 0.18, vUv.y ) * ( 1.0 - smoothstep( 0.45, 1.0, vUv.y ) );
  float horiz = smoothstep( 0.0, 0.12, vUv.x ) * ( 1.0 - smoothstep( 0.88, 1.0, vUv.x ) );
  float a = n * vert * horiz * uOpacity;
  vec3 dir = normalize( vWorldPos - cameraPosition );
  vec3 col = uFogColor + skyGlow( dir, uSunDir, uSunGlow, exp( -dir.y * dir.y * 5.0 ) );
  gl_FragColor = vec4( col, a );
}`;

function buildBand(cfg, fog, sunDir, glow) {
  const uniforms = {
    uTime:     { value: 0 },
    uSpeed:    { value: cfg.speed },
    uScale:    { value: cfg.scale },
    uOpacity:  { value: cfg.opacity },
    uFogColor: { value: new THREE.Color(fog.color) },
    uSunDir:   { value: sunDir.clone().normalize() },
    uSunGlow:  { value: new THREE.Color(glow) },
    uWindDir:  { value: new THREE.Vector2(0.82, 0.57).normalize() },
  };
  const mat = new THREE.ShaderMaterial({
    uniforms, vertexShader: BAND_VERT, fragmentShader: BAND_FRAG,
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(cfg.width, cfg.height), mat);
  mesh.position.set(0, cfg.y, cfg.z);
  mesh.frustumCulled = false;
  return { mesh, uniforms };
}

export function createParticles(fog, sunPosition, glowColor) {
  const sprite = softSprite();
  const mist = buildLayer(MIST, sprite, fog);
  const dust = buildLayer(DUST, sprite, fog);
  const bands = MIST_BANDS.map((b) => buildBand(b, fog, sunPosition, glowColor));

  const group = new THREE.Group();
  group.add(mist.points, dust.points, ...bands.map((b) => b.mesh));

  group.userData.update = (dt, t) => {
    mist.uniforms.uTime.value = t;
    dust.uniforms.uTime.value = t;
    for (const b of bands) b.uniforms.uTime.value = t;
  };
  group.userData.dispose = () => {
    sprite.dispose();
    for (const l of [mist, dust]) { l.points.geometry.dispose(); l.points.material.dispose(); }
    for (const b of bands) { b.mesh.geometry.dispose(); b.mesh.material.dispose(); }
  };
  return group;
}
