import { Analysis, at, timeAtBeat } from '../audio/analysis';
import type { MusicState } from '../audio/engine';
import type { RigW } from '../dance/rig';
import { V3, add, scl, sub } from '../dance/math3d';

/* ------------------------------------------------------------------ */
/*  Projector: a real perspective camera (eye / target / fov / roll)   */
/* ------------------------------------------------------------------ */
export interface CamShot {
  eye: V3;
  tgt: V3;
  fov: number; // vertical field of view, radians
  roll: number; // dutch tilt, radians (positive = clockwise on screen)
}

export const NEAR = 0.12;

const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot3 = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const norm = (a: V3): V3 => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};

export class Projector {
  eye: V3 = [0, 1, 5];
  private r: V3 = [1, 0, 0];
  private u: V3 = [0, 1, 0];
  private f: V3 = [0, 0, -1];
  private focal = 500;
  private sx = 0;
  private sy = 0;
  private cr = 1;
  private sr = 0;

  set(shot: CamShot, W: number, H: number) {
    this.eye = shot.eye;
    const f = norm(sub(shot.tgt, shot.eye));
    let up: V3 = [0, 1, 0];
    if (Math.abs(f[1]) > 0.995) up = [0, 0, -1];
    const r = norm(cross(f, up));
    const u = cross(r, f);
    this.f = f;
    this.r = r;
    this.u = u;
    // on tall/narrow screens, shrink the picture so the dancer still fits
    const Hf = Math.min(H, W * 1.25);
    this.focal = Hf / 2 / Math.tan(shot.fov / 2);
    this.sx = W / 2;
    this.sy = H * 0.46;
    this.cr = Math.cos(shot.roll);
    this.sr = Math.sin(shot.roll);
  }

  /** world -> camera space (x right, y up, z forward), including dutch roll */
  toCam(v: V3): V3 {
    const d = sub(v, this.eye);
    const xc = dot3(d, this.r);
    const yc = dot3(d, this.u);
    const zc = dot3(d, this.f);
    return [xc * this.cr - yc * this.sr, xc * this.sr + yc * this.cr, zc];
  }

  /** camera space -> screen */
  scr(c: V3): { x: number; y: number; ppu: number } {
    const z = Math.max(NEAR, c[2]);
    const k = this.focal / z;
    return { x: this.sx + c[0] * k, y: this.sy - c[1] * k, ppu: k };
  }

  project(v: V3) {
    const c = this.toCam(v);
    const s = this.scr(c);
    return { ...s, z: c[2] };
  }

  /** project a polygon, clipping against the near plane. Returns screen points. */
  poly(pts: V3[]): [number, number][] {
    const cs = pts.map((p) => this.toCam(p));
    const out: V3[] = [];
    for (let i = 0; i < cs.length; i++) {
      const a = cs[i];
      const b = cs[(i + 1) % cs.length];
      const ain = a[2] >= NEAR;
      const bin = b[2] >= NEAR;
      if (ain) out.push(a);
      if (ain !== bin) {
        const t = (NEAR - a[2]) / (b[2] - a[2]);
        out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, NEAR]);
      }
    }
    return out.map((c) => {
      const s = this.scr(c);
      return [s.x, s.y] as [number, number];
    });
  }
}

/* ------------------------------------------------------------------ */
/*  Shots                                                              */
/* ------------------------------------------------------------------ */
interface ShotCtx {
  u: number; // beats since this shot began
  b: number; // song position in beats
  e: number; // section energy 0..1
  bass: number;
  dir: number; // +1 / -1: mirror the move left/right
  r0: number; // random 0..1 per shot
  bpm: number;
  mid: V3; // smoothed body centre
  head: V3; // smoothed head
}

interface ShotDef {
  name: string;
  lo: number;
  hi: number;
  w: number;
  min: number; // minimum bars (4 beats) the shot should last
  big?: boolean; // good for drops
  k?: number; // follow-smoothing speed
  run: (c: ShotCtx) => CamShot;
}

const D2R = Math.PI / 180;
const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const ease = (t: number) => {
  t = clamp(t, 0, 1);
  return t * t * (3 - 2 * t);
};
const fr = (x: number) => x - Math.floor(x);

export const SHOTS: ShotDef[] = [
  {
    name: 'Wide Shot', lo: 0, hi: 0.65, w: 1.6, min: 1,
    run: (c) => ({
      eye: [c.mid[0] * 0.4 + 0.8 * Math.sin(c.u * 0.2) * c.dir, 0.9 + 0.15 * Math.sin(c.u * 0.3), 4.4 - 0.03 * Math.min(c.u, 16)],
      tgt: [c.mid[0] * 0.7, 0.5, 0],
      fov: 34 * D2R, roll: 0,
    }),
  },
  {
    name: 'Orbit', lo: 0.12, hi: 1, w: 1.7, min: 2,
    run: (c) => {
      const rev = lerp(44, 16, c.e);
      const th = (c.r0 - 0.5) * 0.8 + (c.dir * c.u * Math.PI * 2) / rev;
      const r = 3.4 - 0.5 * c.e + 0.25 * Math.sin(c.u * 0.4);
      return {
        eye: [c.mid[0] * 0.5 + Math.sin(th) * r, 0.95 + 0.45 * Math.sin(c.u * 0.33), Math.cos(th) * r],
        tgt: [c.mid[0] * 0.6, 0.55, 0],
        fov: 38 * D2R, roll: 0,
      };
    },
  },
  {
    name: 'Low Hero', lo: 0.3, hi: 1, w: 1.2, min: 1,
    run: (c) => ({
      eye: [c.dir * (1.2 + 0.5 * Math.sin(c.u * 0.2)), 0.14, 2.5 - 0.04 * Math.min(c.u, 12)],
      tgt: [c.mid[0], 0.85, 0],
      fov: 64 * D2R, roll: 0.07 * c.dir * Math.sin(c.u * 0.5),
    }),
  },
  {
    name: 'Overhead', lo: 0.35, hi: 1, w: 0.9, min: 2,
    run: (c) => ({
      eye: [Math.sin(c.u * 0.15) * 0.9, 4.8 - 0.09 * Math.min(c.u, 14), 1.1],
      tgt: [c.mid[0], 0, 0],
      fov: 52 * D2R, roll: c.dir * c.u * 0.09,
    }),
  },
  {
    name: 'Close-Up', lo: 0.2, hi: 1, w: 1.2, min: 1, k: 9,
    run: (c) => {
      const th = c.dir * (0.35 + 0.3 * Math.sin(c.u * 0.3));
      const t: V3 = [c.head[0], c.head[1] - 0.09, c.head[2]];
      const d = 1.4;
      return {
        eye: [t[0] + Math.sin(th) * d, t[1] + 0.04, t[2] + Math.cos(th) * d],
        tgt: t,
        fov: (30 - 6 * c.bass * c.bass) * D2R, roll: 0,
      };
    },
  },
  {
    name: 'Dutch Angle', lo: 0.4, hi: 1, w: 1.0, min: 1,
    run: (c) => ({
      eye: [c.dir * 1.7, 0.8 + 0.2 * Math.sin(c.u * 0.5), 3.0],
      tgt: [c.mid[0], 0.55, 0],
      fov: 44 * D2R, roll: c.dir * (0.28 + 0.12 * Math.sin((c.b * Math.PI) / 2)) + 0.03 * Math.pow(1 - fr(c.b), 3),
    }),
  },
  {
    name: 'Dolly Slide', lo: 0.05, hi: 0.85, w: 1.2, min: 2, k: 7,
    run: (c) => ({
      eye: [2.8 * Math.sin(c.u * 0.26 + c.r0 * 6) , 0.9 + 0.1 * Math.sin(c.u * 0.4), 3.1],
      tgt: [c.mid[0], 0.55, 0],
      fov: 40 * D2R, roll: 0,
    }),
  },
  {
    name: 'Vertigo Zoom', lo: 0.4, hi: 1, w: 0.8, min: 2, big: true,
    run: (c) => {
      const k = ease(c.u / 8);
      const d = c.dir > 0 ? lerp(1.8, 6.2, k) : lerp(6.2, 1.8, k);
      return {
        eye: [c.mid[0] * 0.5, 0.75, d],
        tgt: [c.mid[0] * 0.5, 0.5, 0],
        fov: 2 * Math.atan(2.4 / (2 * d)), // keeps the dancer the same size while the room warps
        roll: 0,
      };
    },
  },
  {
    name: 'Behind Him', lo: 0.1, hi: 0.9, w: 0.8, min: 1,
    run: (c) => ({
      eye: [0.7 * Math.sin(c.u * 0.2) * c.dir, 1.2 + 0.1 * Math.sin(c.u * 0.3), -3.4],
      tgt: [c.mid[0], 0.6, 0],
      fov: 40 * D2R, roll: 0,
    }),
  },
  {
    name: 'Footwork', lo: 0.35, hi: 1, w: 0.8, min: 1,
    run: (c) => ({
      eye: [c.dir * 0.8, 0.12, 2.0],
      tgt: [c.mid[0], 0.25, 0],
      fov: 58 * D2R, roll: 0.1 * c.dir * Math.sin(c.u * 0.4),
    }),
  },
  {
    name: 'Crane Up', lo: 0.2, hi: 1, w: 1.0, min: 2, big: true,
    run: (c) => {
      const k = ease(c.u / 8);
      const th = c.dir * (-0.7 + 0.9 * k);
      const r = lerp(2.6, 3.4, k);
      return {
        eye: [Math.sin(th) * r, lerp(0.15, 3.2, k), Math.cos(th) * r],
        tgt: [c.mid[0], 0.5, 0],
        fov: 46 * D2R, roll: 0,
      };
    },
  },
];

/* ------------------------------------------------------------------ */
/*  The camera operator: picks shots from the song                     */
/* ------------------------------------------------------------------ */
const SEGC = 4; // beats per potential cut (one bar)
const MAXRUN = 4; // bars

function mulberry32(a: number) {
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface SeqItem {
  shot: number;
  start: number; // bar index where this shot began
  dir: number;
  r0: number;
}

export interface CamOut {
  shot: CamShot;
  name: string;
  cut: number; // 0..1, strong right on a cut
  kick: number; // 0..1 beat pulse for FX
}

export class CameraDirector {
  private an: Analysis | null = null;
  private seqTrack: SeqItem[] = [];
  private seqFree: SeqItem[] = [];
  private seed = 1;
  private inst = "";
  private instMode = '';
  private mid: V3 = [0, 0.5, 0];
  private head: V3 = [0, 0.9, 0];
  private lastNow = 0;

  setAnalysis(an: Analysis | null) {
    this.an = an;
    this.seqTrack = [];
    this.seed = an ? Math.floor(an.duration * 977 + an.bpm * 31) : 1;
  }

  private segInfo(seg: number, mode: string): { e: number; drop: boolean } {
    if (mode === 'music' && this.an) {
      const an = this.an;
      const i0 = seg * SEGC + an.downbeat;
      const t0 = timeAtBeat(an, i0);
      const t1 = timeAtBeat(an, i0 + SEGC);
      let e = 0;
      const n = 4;
      for (let k = 0; k < n; k++) e += at(an.energy, an.fps, t0 + ((t1 - t0) * (k + 0.5)) / n);
      e /= n;
      const drop = an.drops.some((d) => d >= t0 - 0.5 && d < t0 + (t1 - t0) * 0.6);
      return { e, drop };
    }
    return { e: 0.45 + 0.5 * (0.5 + 0.5 * Math.sin(seg * 0.9 + 0.5)), drop: seg % 11 === 10 };
  }

  private pick(seg: number, mode: string): SeqItem {
    const seq = mode === 'music' ? this.seqTrack : this.seqFree;
    const s = Math.max(0, seg);
    while (seq.length <= s) {
      const i = seq.length;
      const { e, drop } = this.segInfo(i, mode);
      const rng = mulberry32(this.seed + i * 104729 + (mode === 'music' ? 0 : 17));
      const prev = seq[i - 1];
      const run = prev ? i - prev.start : 0;
      const dir = rng() < 0.5 ? -1 : 1;
      const r0 = rng();
      const roll = rng();

      let cont = false;
      if (prev) {
        const def = SHOTS[prev.shot];
        if (run < def.min) cont = true;
        else if (run < MAXRUN && !(drop && !def.big)) cont = roll < clamp(0.62 - 0.55 * e, 0.05, 0.6);
      }
      if (cont && prev) {
        seq.push(prev);
        continue;
      }

      const recent = seq.slice(-6).map((x) => x.shot);
      const weights = SHOTS.map((d, idx) => {
        let w = d.w * (e >= d.lo && e <= d.hi ? 1 : 0.05);
        if (drop) w *= d.big ? 5 : 0.2;
        if (i === 0) w *= idx === 0 || idx === 1 ? 4 : 0.3;
        if (recent.includes(idx)) w *= 0.03;
        if (prev && idx === prev.shot) w *= 0.001;
        return w;
      });
      const total = weights.reduce((a, b) => a + b, 0);
      let r = rng() * total;
      let chosen = 0;
      for (let k = 0; k < weights.length; k++) {
        r -= weights[k];
        if (r <= 0) {
          chosen = k;
          break;
        }
      }
      seq.push({ shot: chosen, start: i, dir, r0 });
    }
    return seq[s];
  }

  update(m: MusicState, now: number, rig: RigW, intensity: number): CamOut {
    const dt = clamp((now - this.lastNow) / 1000, 0, 0.1);
    this.lastNow = now;
    const mode = m.mode;
    const b = m.dancePos;
    const e = clamp(intensity, 0, 1);

    let item: SeqItem;
    if (mode === 'idle') item = { shot: 0, start: -1, dir: 1, r0: 0.3 };
    else item = this.pick(Math.floor(b / SEGC), mode);
    const def = SHOTS[item.shot];
    const instKey = item.start + ':' + item.shot;
    const mid0: V3 = scl(add(rig.pelvis, rig.head), 0.5);
    const fresh = instKey !== this.inst || mode !== this.instMode;
    if (fresh) {
      this.inst = instKey;
      this.instMode = mode;
      this.mid = mid0;
      this.head = rig.head;
    } else {
      const a = 1 - Math.exp(-dt * (def.k ?? 5));
      this.mid = add(this.mid, scl(sub(mid0, this.mid), a));
      this.head = add(this.head, scl(sub(rig.head, this.head), a));
    }

    const u = mode === 'idle' ? Math.max(0, b) : Math.max(0, b - item.start * SEGC);
    const shot = def.run({ u, b, e, bass: m.bass, dir: item.dir, r0: item.r0, bpm: m.bpm, mid: this.mid, head: this.head });

    // --- beat-driven camera "feel" ---
    const beatPulse = Math.pow(1 - fr(b), 3);
    const barPulse = Math.pow(1 - fr(b / 4), 5);
    const pulse = mode === 'idle' ? 0 : (0.02 + 0.045 * e) * beatPulse + 0.035 * barPulse * e + 0.04 * m.bass * m.bass;
    let fov = shot.fov * (1 - pulse);

    // whip-in at the start of each cut
    const uu = mode === 'idle' ? 99 : b - item.start * SEGC;
    const cut = mode === 'idle' ? 0 : Math.pow(clamp(1 - uu / 0.45, 0, 1), 2);
    fov *= 1 + 0.22 * cut * (item.shot === 7 ? 0 : 1);

    // handheld shake grows with energy and bass
    const t = now / 1000;
    const amp = mode === 'idle' ? 0.004 : 0.004 + 0.012 * e + 0.012 * m.bass * m.bass;
    const sh: V3 = [
      amp * (Math.sin(t * 13.1) + 0.6 * Math.sin(t * 7.3 + 1.3)),
      amp * (Math.sin(t * 11.7 + 2) + 0.6 * Math.sin(t * 5.9)),
      amp * 0.5 * Math.sin(t * 9.1 + 0.7),
    ];
    const eye = add(shot.eye, sh);
    const tgt = add(shot.tgt, scl(sh, -0.6));
    const roll = shot.roll + amp * 0.35 * Math.sin(t * 8.3) + (mode === 'idle' ? 0 : 0.012 * e * Math.sin(b * Math.PI));

    return { shot: { eye, tgt, fov: clamp(fov, 10 * D2R, 95 * D2R), roll }, name: def.name, cut, kick: beatPulse };
  }
}
