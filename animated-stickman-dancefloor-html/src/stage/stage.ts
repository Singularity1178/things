import type { MusicState } from '../audio/engine';

export interface Layout {
  W: number;
  H: number;
  U: number; // pixels per body unit
  cx: number;
  fy: number; // floor line where the dancer stands
  horizon: number;
}

export function makeLayout(W: number, H: number): Layout {
  const U = Math.min(H * 0.43, W * 0.62);
  return { W, H, U, cx: W / 2, fy: H * 0.82, horizon: H * 0.47 };
}

interface Confetto {
  x: number; y: number; vx: number; vy: number; rot: number; vr: number; w: number; h: number; hue: number; life: number;
}

export interface StageState {
  confetti: Confetto[];
  flash: number;
  lastBeat: number;
}

export const makeStageState = (): StageState => ({ confetti: [], flash: 0, lastBeat: -1 });

const TAU = Math.PI * 2;
const fr = (x: number) => x - Math.floor(x);
const hsl = (h: number, s: number, l: number, a = 1) => `hsla(${((Math.round(h) % 360) + 360) % 360},${s}%,${l}%,${a})`;
const hash = (a: number, b: number, c = 0) => {
  let h = (a * 374761393 + b * 668265263 + c * 2147483647) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
};

export function hueOf(m: MusicState) {
  return (210 + m.dancePos * 6) % 360;
}

export function burst(st: StageState, L: Layout) {
  for (let i = 0; i < 140; i++) {
    st.confetti.push({
      x: Math.random() * L.W, y: -20 - Math.random() * L.H * 0.3,
      vx: (Math.random() - 0.5) * 140, vy: 120 + Math.random() * 260,
      rot: Math.random() * TAU, vr: (Math.random() - 0.5) * 12,
      w: 6 + Math.random() * 7, h: 4 + Math.random() * 6, hue: Math.random() * 360, life: 5 + Math.random() * 3,
    });
  }
}

/* ------------------------------------------------------------------ */
/*  BACKDROP                                                          */
/* ------------------------------------------------------------------ */
export function drawBackdrop(ctx: CanvasRenderingContext2D, L: Layout, m: MusicState, st: StageState, time: number) {
  const { W, H, horizon, cx, fy, U } = L;
  const hue = hueOf(m);
  const bass = m.bass;

  // beat flash bookkeeping
  const beatIdx = Math.floor(m.dancePos);
  if (beatIdx !== st.lastBeat) {
    st.lastBeat = beatIdx;
    if (beatIdx % 4 === 0) st.flash = 1;
  }

  // wall
  const g = ctx.createLinearGradient(0, 0, 0, horizon);
  g.addColorStop(0, hsl(hue + 230, 80, 52));
  g.addColorStop(0.55, hsl(hue + 330, 90, 64));
  g.addColorStop(1, hsl(hue + 20, 95, 74));
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, horizon + 2);

  // LED wall panels
  const cols = 16;
  const pw = W / cols;
  for (let i = 0; i < cols; i++) {
    const wave = 0.5 + 0.5 * Math.sin(m.dancePos * Math.PI * 0.5 + i * 0.55);
    const pulse = 0.12 + 0.28 * wave * (0.5 + 0.8 * bass);
    ctx.fillStyle = hsl(hue + i * 14 + 60, 100, 82, pulse);
    ctx.fillRect(i * pw + 3, H * 0.2, pw - 6, horizon - H * 0.2);
  }

  // big halo behind the dancer so the hollow head always reads
  const hy = fy - L.U * 0.55;
  const halo = ctx.createRadialGradient(cx, hy, U * 0.05, cx, hy, U * (1.15 + 0.12 * bass));
  halo.addColorStop(0, 'rgba(255,255,255,0.95)');
  halo.addColorStop(0.45, 'rgba(255,255,255,0.55)');
  halo.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = halo;
  ctx.fillRect(0, 0, W, horizon + 4);

  // sweeping beams
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  const beams = 7;
  for (let i = 0; i < beams; i++) {
    const ax = (W * (i + 0.5)) / beams;
    const ang = Math.sin(time * 0.8 + i * 1.4 + m.dancePos * 0.25) * 0.55 + (i - beams / 2) * 0.08;
    const len = H * 1.05;
    const ex = ax + Math.sin(ang) * len, ey = Math.cos(ang) * len;
    const hw = H * (0.05 + 0.04 * bass);
    const nx = Math.cos(ang), ny = -Math.sin(ang);
    const bg = ctx.createLinearGradient(ax, 0, ex, ey);
    bg.addColorStop(0, hsl(hue + i * 55, 100, 78, 0.55));
    bg.addColorStop(1, hsl(hue + i * 55, 100, 78, 0));
    ctx.fillStyle = bg;
    ctx.beginPath();
    ctx.moveTo(ax - 4, -4);
    ctx.lineTo(ax + 4, -4);
    ctx.lineTo(ex + nx * hw, ey + ny * hw);
    ctx.lineTo(ex - nx * hw, ey - ny * hw);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();

  // equalizer strip along the base of the wall
  const eq = m.eq;
  const bw = W / eq.length;
  for (let i = 0; i < eq.length; i++) {
    const v = Math.min(1, eq[i]);
    const h = (0.03 + v * 0.17) * H;
    ctx.fillStyle = hsl(hue + i * 6 + 120, 100, 84, 0.8);
    ctx.fillRect(i * bw + 1.5, horizon - h, bw - 3, h);
  }

  drawDiscoBall(ctx, L, m, time);
}

function drawDiscoBall(ctx: CanvasRenderingContext2D, L: Layout, m: MusicState, time: number) {
  const { W, H, horizon } = L;
  const R = Math.min(W, H) * 0.062;
  const cx = W / 2;
  const cy = H * 0.1;
  const rot = time * 0.7;
  // wall spots
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  for (let i = 0; i < 22; i++) {
    const a = rot * 0.5 + (i * TAU) / 22;
    const x = cx + Math.cos(a) * W * (0.3 + 0.2 * Math.sin(i * 1.7));
    const y = horizon * (0.45 + 0.4 * Math.sin(a * 1.3 + i));
    const r = 5 + 8 * hash(i, 3) + 6 * m.bass;
    const sg = ctx.createRadialGradient(x, y, 0, x, y, r);
    sg.addColorStop(0, 'rgba(255,255,255,0.9)');
    sg.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = sg;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  ctx.restore();

  ctx.strokeStyle = 'rgba(40,20,70,0.6)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(cx, 0);
  ctx.lineTo(cx, cy - R);
  ctx.stroke();

  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, TAU);
  ctx.clip();
  ctx.fillStyle = '#9aa0c8';
  ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
  const LAT = 8, LON = 18;
  const fl = Math.floor(time * 4);
  for (let i = 0; i < LAT; i++) {
    const la0 = -Math.PI / 2 + (i * Math.PI) / LAT;
    const la1 = -Math.PI / 2 + ((i + 1) * Math.PI) / LAT;
    for (let j = 0; j < LON; j++) {
      const lo0 = rot + (j * TAU) / LON;
      const lo1 = rot + ((j + 1) * TAU) / LON;
      const mid = (lo0 + lo1) / 2;
      const facing = Math.cos(mid);
      if (facing <= 0) continue;
      const P = (la: number, lo: number): [number, number] => [cx + R * Math.cos(la) * Math.sin(lo), cy - R * Math.sin(la)];
      const sp = hash(i, j, fl) > 0.93 ? 1 : 0;
      const light = 38 + 34 * facing * (0.6 + 0.4 * hash(i, j)) + sp * 40;
      ctx.fillStyle = `hsl(${220 + 30 * hash(i, j)},35%,${Math.min(98, light)}%)`;
      const a = P(la0, lo0), b = P(la0, lo1), c = P(la1, lo1), d = P(la1, lo0);
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
      ctx.lineTo(c[0], c[1]);
      ctx.lineTo(d[0], d[1]);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = 'rgba(30,20,60,0.5)';
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }
  }
  ctx.restore();
  ctx.strokeStyle = 'rgba(40,20,70,0.55)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, TAU);
  ctx.stroke();
}

/* ------------------------------------------------------------------ */
/*  FLOOR                                                             */
/* ------------------------------------------------------------------ */
const KF = 0.13;
const TILE = 0.55;
const scAt = (z: number) => 1 / (1 + z * KF);

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

export function drawFloor(ctx: CanvasRenderingContext2D, L: Layout, m: MusicState, time: number) {
  const { W, H, horizon, fy, cx, U } = L;
  const hue = hueOf(m);
  const pat = m.mode === 'idle' ? 2 : Math.floor(Math.max(0, m.dancePos) / 16) % 5;
  const yAt = (z: number) => horizon + (fy - horizon) * scAt(z);

  // base
  const bg = ctx.createLinearGradient(0, horizon, 0, H);
  bg.addColorStop(0, hsl(hue + 40, 80, 70));
  bg.addColorStop(1, hsl(hue + 40, 90, 50));
  ctx.fillStyle = bg;
  ctx.fillRect(0, horizon, W, H - horizon);

  const scN = (H - horizon) / (fy - horizon);
  const zN = (1 / scN - 1) / KF;
  const rNear = Math.floor(zN / TILE) - 1;
  const rFar = 56;
  for (let r = rNear; r <= rFar; r++) {
    const z0 = (r - 0.5) * TILE, z1 = (r + 0.5) * TILE;
    const y0 = yAt(z0), y1 = yAt(z1);
    if (y1 > H || y0 - y1 < 1.2) continue;
    const s0 = scAt(z0), s1 = scAt(z1);
    const half = W / 2 / (U * s1);
    const cmax = Math.ceil(half / TILE) + 1;
    for (let c = -cmax; c <= cmax; c++) {
      const xa = (c - 0.5) * TILE, xb = (c + 0.5) * TILE;
      ctx.fillStyle = tileColor(c, r, m.dancePos, hue, m.bass, pat);
      ctx.beginPath();
      ctx.moveTo(cx + xa * U * s0, y0);
      ctx.lineTo(cx + xb * U * s0, y0);
      ctx.lineTo(cx + xb * U * s1, y1);
      ctx.lineTo(cx + xa * U * s1, y1);
      ctx.closePath();
      ctx.fill();
    }
  }
  // grout
  ctx.strokeStyle = 'rgba(25,5,50,0.55)';
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  const yFarEdge = yAt((rFar + 0.5) * TILE);
  const sFar = scAt((rFar + 0.5) * TILE);
  const sNear = scAt((rNear - 0.5) * TILE);
  const cm = Math.ceil(W / 2 / (U * sFar) / TILE) + 1;
  for (let c = -cm; c <= cm; c++) {
    const x = (c - 0.5) * TILE;
    ctx.moveTo(cx + x * U * sNear, yAt((rNear - 0.5) * TILE));
    ctx.lineTo(cx + x * U * sFar, yFarEdge);
  }
  for (let r = rNear; r <= rFar + 1; r++) {
    const y = yAt((r - 0.5) * TILE);
    if (y > H + 2) continue;
    ctx.moveTo(0, y);
    ctx.lineTo(W, y);
  }
  ctx.stroke();

  // distance haze
  const hz = ctx.createLinearGradient(0, horizon, 0, horizon + (fy - horizon) * 0.95);
  hz.addColorStop(0, hsl(hue + 40, 85, 76, 1));
  hz.addColorStop(0.35, hsl(hue + 40, 85, 76, 0.85));
  hz.addColorStop(1, hsl(hue + 40, 85, 76, 0));
  ctx.fillStyle = hz;
  ctx.fillRect(0, horizon, W, (fy - horizon) * 0.95);

  // spot pool under the dancer
  ctx.save();
  ctx.globalCompositeOperation = 'screen';
  ctx.translate(cx, fy);
  ctx.scale(1, 0.24);
  const pool = ctx.createRadialGradient(0, 0, 0, 0, 0, U * 1.25);
  pool.addColorStop(0, 'rgba(255,255,255,0.8)');
  pool.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = pool;
  ctx.beginPath();
  ctx.arc(0, 0, U * 1.25, 0, TAU);
  ctx.fill();
  ctx.restore();

  // speakers
  const sc = 0.62;
  const sy = horizon + (fy - horizon) * 0.62;
  for (const side of [-1, 1]) {
    const sw = U * 0.5 * sc * 1.3, sh = U * 1.05 * sc * 1.3;
    const sx = cx + side * Math.min(W * 0.4, U * 2.3) - sw / 2;
    ctx.fillStyle = '#1b1038';
    ctx.strokeStyle = hsl(hue + 100, 100, 65);
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(sx, sy - sh, sw, sh, 8);
    ctx.fill();
    ctx.stroke();
    const woof = (yy: number, rr: number) => {
      const pr = rr * (1 + 0.1 * m.bass);
      const wg = ctx.createRadialGradient(sx + sw / 2, yy, 1, sx + sw / 2, yy, pr);
      wg.addColorStop(0, hsl(hue + 100, 100, 70));
      wg.addColorStop(0.35, '#2a1a55');
      wg.addColorStop(1, '#0e0620');
      ctx.fillStyle = wg;
      ctx.beginPath();
      ctx.arc(sx + sw / 2, yy, pr, 0, TAU);
      ctx.fill();
    };
    woof(sy - sh * 0.7, sw * 0.28);
    woof(sy - sh * 0.3, sw * 0.38);
  }
  void time;
}

/* ------------------------------------------------------------------ */
/*  FRONT FX                                                          */
/* ------------------------------------------------------------------ */
export function drawFront(ctx: CanvasRenderingContext2D, L: Layout, st: StageState, dt: number) {
  const { W, H } = L;
  // confetti
  for (let i = st.confetti.length - 1; i >= 0; i--) {
    const p = st.confetti[i];
    p.life -= dt;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
    p.vx += Math.sin(p.rot * 2) * 30 * dt;
    p.rot += p.vr * dt;
    if (p.life <= 0 || p.y > H + 20) {
      st.confetti.splice(i, 1);
      continue;
    }
    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.rotate(p.rot);
    ctx.scale(1, Math.abs(Math.cos(p.rot * 1.7)) * 0.9 + 0.1);
    ctx.fillStyle = hsl(p.hue, 100, 55);
    ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
    ctx.restore();
  }
  // downbeat flash
  if (st.flash > 0.01) {
    ctx.fillStyle = `rgba(255,255,255,${0.14 * st.flash})`;
    ctx.fillRect(0, 0, W, H);
    st.flash *= Math.pow(0.0005, dt);
  }
  // vignette
  const v = ctx.createRadialGradient(W / 2, H * 0.55, Math.min(W, H) * 0.45, W / 2, H * 0.55, Math.max(W, H) * 0.8);
  v.addColorStop(0, 'rgba(30,0,60,0)');
  v.addColorStop(1, 'rgba(30,0,60,0.4)');
  ctx.fillStyle = v;
  ctx.fillRect(0, 0, W, H);
}
