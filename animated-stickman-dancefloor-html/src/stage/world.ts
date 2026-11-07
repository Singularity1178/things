import type { MusicState } from '../audio/engine';
import { V3 } from '../dance/math3d';
import { Projector } from './camera';
import { TAU, drawMirrorBall, fr, hash, hsl, hueOf } from './stage';

/**
 * A real 3D room the camera can fly around in.
 * Units are "body units" (the dancer is ~1 tall). y up, +z toward the default viewer.
 */
export const ROOM = { x0: -7, x1: 7, z0: -6, z1: 7.5, h: 6 };
const TILE = 0.55;
const BALL: V3 = [0, 4.3, -0.3];
const BALL_R = 0.42;

type Pt = [number, number][];

function fillPoly(ctx: CanvasRenderingContext2D, pts: Pt, fill: string, stroke?: string, lw = 1.5) {
  if (pts.length < 3) return;
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lw;
    ctx.stroke();
  }
}

const onScreen = (pts: Pt, W: number, H: number) => {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of pts) {
    if (p[0] < minX) minX = p[0];
    if (p[0] > maxX) maxX = p[0];
    if (p[1] < minY) minY = p[1];
    if (p[1] > maxY) maxY = p[1];
  }
  return !(maxX < 0 || minX > W || maxY < 0 || minY > H);
};

// ---- tiny colour mixer so wall gradients blend in RGB like a real gradient ----
function hslToRgb(h: number, s: number, l: number): V3 {
  h = (((h % 360) + 360) % 360) / 360;
  s /= 100;
  l /= 100;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h * 12) % 12;
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0) * 255, f(8) * 255, f(4) * 255];
}
function mixRgb(a: V3, b: V3, t: number): string {
  const c = (i: number) => Math.round(a[i] + (b[i] - a[i]) * t);
  return `rgb(${c(0)},${c(1)},${c(2)})`;
}

function tileColor(c: number, r: number, pos: number, hue: number, bass: number, pat: number): string {
  let g = 0.5;
  let h = hue;
  switch (pat) {
    case 0: {
      const d = Math.hypot(c, r);
      g = 0.5 + 0.5 * Math.sin(d * 1.15 - pos * Math.PI);
      h = hue + d * 16;
      break;
    }
    case 1: {
      const chk = (c + r) & 1;
      const on = (Math.floor(pos) & 1) === chk;
      g = on ? 0.95 - 0.5 * fr(pos) : 0.1;
      h = hue + (chk ? 0 : 150);
      break;
    }
    case 2:
      g = 0.5 + 0.5 * Math.sin(c * 0.8 + r * 0.8 - pos * 2);
      h = hue + c * 22 + r * 12;
      break;
    case 3: {
      const k = hash(c, r, Math.floor(pos * 2));
      g = k > 0.62 ? 1 : 0.15;
      h = k * 360 + hue;
      break;
    }
    default:
      g = 0.5 + 0.5 * Math.sin(c * 1.1 - pos * 3);
      h = hue + r * 12 + c * 5;
  }
  const l = 40 + 28 * g + 10 * bass;
  return hsl(h, 95, Math.min(82, l));
}

/* ------------------------------------------------------------------ */
/*  Floor                                                              */
/* ------------------------------------------------------------------ */
export function drawFloor3D(ctx: CanvasRenderingContext2D, P: Projector, W: number, H: number, m: MusicState) {
  const hue = hueOf(m);
  const pat = m.mode === 'idle' ? 2 : Math.floor(Math.max(0, m.dancePos) / 16) % 5;
  const { x0, x1, z0, z1 } = ROOM;
  const cMax = Math.ceil(Math.max(-x0, x1) / TILE);
  const rMin = Math.floor(z0 / TILE);
  const rMax = Math.ceil(z1 / TILE);
  const grout = new Path2D();
  for (let j = rMin; j <= rMax; j++) {
    const za = Math.max(z0, (j - 0.5) * TILE);
    const zb = Math.min(z1, (j + 0.5) * TILE);
    if (zb - za < 0.01) continue;
    for (let c = -cMax; c <= cMax; c++) {
      const xa = Math.max(x0, (c - 0.5) * TILE);
      const xb = Math.min(x1, (c + 0.5) * TILE);
      if (xb - xa < 0.01) continue;
      const pts = P.poly([[xa, 0, za], [xb, 0, za], [xb, 0, zb], [xa, 0, zb]]);
      if (pts.length < 3 || !onScreen(pts, W, H)) continue;
      fillPoly(ctx, pts, tileColor(c, -j, m.dancePos, hue, m.bass, pat));
      grout.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) grout.lineTo(pts[i][0], pts[i][1]);
      grout.closePath();
    }
  }
  ctx.strokeStyle = 'rgba(25,5,50,0.5)';
  ctx.lineWidth = 1.3;
  ctx.stroke(grout);
}

/** A glowing spot-pool on the floor and the dancer's contact shadow. */
export function drawFloorSpot(ctx: CanvasRenderingContext2D, P: Projector, cx: number, lift: number) {
  const ring = (r: number, n = 32): V3[] => {
    const out: V3[] = [];
    for (let i = 0; i < n; i++) out.push([cx + Math.cos((i / n) * TAU) * r, 0, Math.sin((i / n) * TAU) * r]);
    return out;
  };
  // pool
  const pts = P.poly(ring(1.5));
  if (pts.length >= 3) {
    const c = P.project([cx, 0, 0]);
    let R = 1;
    for (const p of pts) R = Math.max(R, Math.hypot(p[0] - c.x, p[1] - c.y));
    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    ctx.clip();
    const g = ctx.createRadialGradient(c.x, c.y, 0, c.x, c.y, R);
    g.addColorStop(0, 'rgba(255,255,255,0.75)');
    g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g;
    ctx.fillRect(c.x - R, c.y - R, R * 2, R * 2);
    ctx.restore();
  }
  // shadow
  const sh = P.poly(ring(0.3 * (1 - 0.3 * lift), 20));
  fillPoly(ctx, sh, `rgba(0,0,0,${0.3 * (1 - 0.6 * lift)})`);
}

/* ------------------------------------------------------------------ */
/*  Walls, ceiling                                                     */
/* ------------------------------------------------------------------ */
interface Wall {
  o: V3;
  du: V3;
  len: number;
}
const WALLS: Wall[] = [
  { o: [ROOM.x0, 0, ROOM.z0], du: [ROOM.x1 - ROOM.x0, 0, 0], len: ROOM.x1 - ROOM.x0 }, // back
  { o: [ROOM.x1, 0, ROOM.z1], du: [-(ROOM.x1 - ROOM.x0), 0, 0], len: ROOM.x1 - ROOM.x0 }, // front
  { o: [ROOM.x0, 0, ROOM.z1], du: [0, 0, -(ROOM.z1 - ROOM.z0)], len: ROOM.z1 - ROOM.z0 }, // left
  { o: [ROOM.x1, 0, ROOM.z0], du: [0, 0, ROOM.z1 - ROOM.z0], len: ROOM.z1 - ROOM.z0 }, // right
];
const wp = (w: Wall, u: number, v: number): V3 => [w.o[0] + w.du[0] * u, w.o[1] + v * ROOM.h, w.o[2] + w.du[2] * u];

export function drawWalls3D(ctx: CanvasRenderingContext2D, P: Projector, W: number, H: number, m: MusicState, time: number) {
  const hue = hueOf(m);
  const bass = m.bass;
  const cTop = hslToRgb(hue + 230, 80, 52);
  const cMid = hslToRgb(hue + 330, 90, 64);
  const cLow = hslToRgb(hue + 20, 95, 74);
  const colAt = (v: number) => {
    const t = 1 - v; // 0 = top, 1 = floor line
    return t < 0.55 ? mixRgb(cTop, cMid, t / 0.55) : mixRgb(cMid, cLow, (t - 0.55) / 0.45);
  };
  const BANDS = 8;
  const eq = m.eq;

  for (let wi = 0; wi < WALLS.length; wi++) {
    const w = WALLS[wi];
    // gradient bands (slightly overdraw to avoid seams)
    for (let k = 0; k < BANDS; k++) {
      const v0 = k / BANDS, v1 = (k + 1) / BANDS + 0.004;
      const pts = P.poly([wp(w, 0, v0), wp(w, 1, v0), wp(w, 1, v1), wp(w, 0, v1)]);
      if (pts.length < 3 || !onScreen(pts, W, H)) continue;
      fillPoly(ctx, pts, colAt((v0 + v1) / 2));
    }
    // LED panels
    const n = Math.round(w.len);
    for (let i = 0; i < n; i++) {
      const wave = 0.5 + 0.5 * Math.sin(m.dancePos * Math.PI * 0.5 + (i + wi * 5) * 0.55);
      const pulse = 0.12 + 0.28 * wave * (0.5 + 0.8 * bass);
      const u0 = (i + 0.04) / n, u1 = (i + 0.96) / n;
      const pts = P.poly([wp(w, u0, 0.25), wp(w, u1, 0.25), wp(w, u1, 0.8), wp(w, u0, 0.8)]);
      if (pts.length < 3 || !onScreen(pts, W, H)) continue;
      fillPoly(ctx, pts, hsl(hue + i * 14 + 60, 100, 82, pulse));
    }
    // equalizer strip along the base
    const bars = eq.length;
    for (let i = 0; i < bars; i++) {
      const vv = Math.min(1, eq[i]);
      const hh = (0.03 + vv * 0.17) * 3 / ROOM.h * 1.2;
      const u0 = (i + 0.08) / bars, u1 = (i + 0.92) / bars;
      const pts = P.poly([wp(w, u0, 0), wp(w, u1, 0), wp(w, u1, hh), wp(w, u0, hh)]);
      if (pts.length < 3 || !onScreen(pts, W, H)) continue;
      fillPoly(ctx, pts, hsl(hue + i * 6 + 120, 100, 84, 0.85));
    }
  }

  // ceiling
  const { x0, x1, z0, z1, h } = ROOM;
  const ce = P.poly([[x0, h, z0], [x1, h, z0], [x1, h, z1], [x0, h, z1]]);
  fillPoly(ctx, ce, hsl(hue + 250, 55, 24));
  // ceiling lamps
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  for (let i = 0; i < 6; i++) {
    for (let j = 0; j < 5; j++) {
      const p = P.project([x0 + 1.2 + i * 2.32, h - 0.01, z0 + 1 + j * 3.2]);
      if (p.z < 0.2) continue;
      const r = Math.max(2, 0.28 * p.ppu);
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
      const hh = hue + (i * 40 + j * 70) + time * 20;
      g.addColorStop(0, hsl(hh, 100, 85, 0.5 + 0.4 * bass));
      g.addColorStop(1, hsl(hh, 100, 70, 0));
      ctx.fillStyle = g;
      ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
    }
  }
  ctx.restore();
}

/** light spots thrown by the disco ball onto the room surfaces */
export function drawBallSpots(ctx: CanvasRenderingContext2D, P: Projector, m: MusicState, time: number) {
  const rot = time * 0.7;
  const { x0, x1, z0, z1, h } = ROOM;
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  for (let i = 0; i < 26; i++) {
    const a = rot * 0.5 + (i * TAU) / 26;
    const el = Math.sin(a * 1.3 + i) * 0.55 + 0.05;
    const d: V3 = [Math.cos(a) * Math.cos(el), Math.sin(el), Math.sin(a) * Math.cos(el)];
    // ray to the room box
    let t = Infinity;
    const test = (num: number, den: number) => {
      if (Math.abs(den) > 1e-6) {
        const tt = num / den;
        if (tt > 0 && tt < t) t = tt;
      }
    };
    test((d[0] > 0 ? x1 : x0) - BALL[0], d[0]);
    test((d[2] > 0 ? z1 : z0) - BALL[2], d[2]);
    test((d[1] > 0 ? h : 0) - BALL[1], d[1]);
    if (!isFinite(t)) continue;
    const pos: V3 = [BALL[0] + d[0] * t, BALL[1] + d[1] * t, BALL[2] + d[2] * t];
    const p = P.project(pos);
    if (p.z < 0.2) continue;
    const r = Math.max(2, (0.1 + 0.12 * hash(i, 3) + 0.07 * m.bass) * p.ppu);
    const sg = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
    sg.addColorStop(0, 'rgba(255,255,255,0.9)');
    sg.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = sg;
    ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
  }
  ctx.restore();
}

/* ------------------------------------------------------------------ */
/*  Beams                                                              */
/* ------------------------------------------------------------------ */
export function drawBeams3D(ctx: CanvasRenderingContext2D, P: Projector, m: MusicState, time: number, dx: number) {
  const hue = hueOf(m);
  const n = 7;
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  for (let i = 0; i < n; i++) {
    const sa = (i / n) * TAU;
    const S: V3 = [Math.cos(sa) * 3.6, ROOM.h - 0.05, Math.sin(sa) * 3.6 - 0.3];
    const ph = time * 0.55 + i * 1.25 + m.dancePos * 0.12;
    const rr = 0.8 + 1.6 * (0.5 + 0.5 * Math.sin(time * 0.7 + i * 1.4));
    const G: V3 = [dx + Math.cos(ph) * rr, 0, Math.sin(ph) * rr];
    const dxv = G[0] - S[0], dzv = G[2] - S[2];
    const dl = Math.hypot(dxv, dzv) || 1;
    const px = -dzv / dl, pz = dxv / dl; // horizontal perpendicular
    const hw = 0.3 + 0.18 * m.bass;
    const pts = P.poly([
      [S[0] + px * 0.03, S[1], S[2] + pz * 0.03], [S[0] - px * 0.03, S[1], S[2] - pz * 0.03],
      [G[0] - px * hw, 0, G[2] - pz * hw], [G[0] + px * hw, 0, G[2] + pz * hw],
    ]);
    if (pts.length < 3) continue;
    const a = P.project(S);
    const b = P.project(G);
    const col = hue + i * 55;
    if (a.z > 0.3 && b.z > 0.3) {
      const g = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
      g.addColorStop(0, hsl(col, 100, 78, 0.5));
      g.addColorStop(1, hsl(col, 100, 78, 0.05));
      fillPoly(ctx, pts, g as unknown as string);
    } else {
      fillPoly(ctx, pts, hsl(col, 100, 78, 0.18));
    }
  }
  ctx.restore();
}

/* ------------------------------------------------------------------ */
/*  Objects: speakers, disco ball                                      */
/* ------------------------------------------------------------------ */
const SPK = { w: 0.95, h: 1.9, d: 0.85, x: 2.9, z: -1.8 };

export function speakerDepth(P: Projector, side: number) {
  return P.toCam([side * SPK.x, SPK.h / 2, SPK.z])[2];
}

export function drawSpeaker3D(ctx: CanvasRenderingContext2D, P: Projector, W: number, H: number, side: number, m: MusicState) {
  const hue = hueOf(m);
  const cx = side * SPK.x, hw = SPK.w / 2, hd = SPK.d / 2, hh = SPK.h;
  const x0 = cx - hw, x1 = cx + hw, z0 = SPK.z - hd, z1 = SPK.z + hd;
  const faces: { pts: V3[]; n: V3; shade: number }[] = [
    { pts: [[x0, 0, z1], [x1, 0, z1], [x1, hh, z1], [x0, hh, z1]], n: [0, 0, 1], shade: 0 },
    { pts: [[x1, 0, z0], [x0, 0, z0], [x0, hh, z0], [x1, hh, z0]], n: [0, 0, -1], shade: 6 },
    { pts: [[x0, 0, z0], [x0, 0, z1], [x0, hh, z1], [x0, hh, z0]], n: [-1, 0, 0], shade: 3 },
    { pts: [[x1, 0, z1], [x1, 0, z0], [x1, hh, z0], [x1, hh, z1]], n: [1, 0, 0], shade: 3 },
    { pts: [[x0, hh, z1], [x1, hh, z1], [x1, hh, z0], [x0, hh, z0]], n: [0, 1, 0], shade: 9 },
  ];
  const e = P.eye;
  for (const f of faces) {
    const c = f.pts[0];
    if ((e[0] - c[0]) * f.n[0] + (e[1] - c[1]) * f.n[1] + (e[2] - c[2]) * f.n[2] <= 0) continue;
    const pts = P.poly(f.pts);
    if (pts.length < 3 || !onScreen(pts, W, H)) continue;
    fillPoly(ctx, pts, `hsl(${(260) % 360},55%,${12 + f.shade}%)`, hsl(hue + 100, 100, 65), 2.5);
  }
  // woofers on the front face
  if (e[2] > z1) {
    const woof = (y: number, r: number) => {
      const pr = r * (1 + 0.1 * m.bass);
      const ring = (k: number): V3[] => {
        const o: V3[] = [];
        for (let i = 0; i < 24; i++) o.push([cx + Math.cos((i / 24) * TAU) * pr * k, y + Math.sin((i / 24) * TAU) * pr * k, z1 + 0.005]);
        return o;
      };
      fillPoly(ctx, P.poly(ring(1)), hsl(hue + 100, 100, 62));
      fillPoly(ctx, P.poly(ring(0.78)), '#2a1a55');
      fillPoly(ctx, P.poly(ring(0.32)), hsl(hue + 100, 100, 70));
    };
    woof(hh * 0.74, 0.17);
    woof(hh * 0.33, 0.27);
  }
}

export function ballDepth(P: Projector) {
  return P.toCam(BALL)[2];
}

export function drawBall3D(ctx: CanvasRenderingContext2D, P: Projector, time: number) {
  const p = P.project(BALL);
  if (p.z < 0.3) return;
  const top = P.project([BALL[0], ROOM.h, BALL[2]]);
  const R = BALL_R * p.ppu;
  ctx.strokeStyle = 'rgba(40,20,70,0.6)';
  ctx.lineWidth = Math.max(1, 0.012 * p.ppu);
  ctx.beginPath();
  ctx.moveTo(top.x, top.y);
  ctx.lineTo(p.x, p.y - R);
  ctx.stroke();
  drawMirrorBall(ctx, p.x, p.y, R, time);
}
