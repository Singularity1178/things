import { Analysis, timeAtBeat, at } from '../audio/analysis';
import type { MusicState } from '../audio/engine';
import { IDLE, MOVES, Move, evalMove, sm } from './moves';
import { BASE, Pose, lerpPose } from './pose';

const SEG = 8; // beats per move
const BLEND = 0.8; // beats to blend between moves

function mulberry32(a: number) {
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface DirectorOut {
  pose: Pose;
  moveName: string;
  nextName: string;
  intensity: number;
}

export class Director {
  private an: Analysis | null = null;
  private seqTrack: number[] = [];
  private seqFree: number[] = [];
  private seed = 1;
  private lastMode = '';
  private fromPose: Pose = { ...BASE };
  private fromT = 0;
  private lastPose: Pose = { ...BASE };
  locked: number | null = null;

  setAnalysis(an: Analysis | null) {
    this.an = an;
    this.seqTrack = [];
    this.seed = an ? Math.floor(an.duration * 1000 + an.bpm * 13) : 1;
  }

  private segInfo(seg: number, mode: string): { e: number; drop: boolean } {
    if (mode === 'music' && this.an) {
      const an = this.an;
      const i0 = seg * SEG + an.downbeat;
      const t0 = timeAtBeat(an, i0);
      const t1 = timeAtBeat(an, i0 + SEG);
      let e = 0;
      const n = 6;
      for (let k = 0; k < n; k++) e += at(an.energy, an.fps, t0 + ((t1 - t0) * (k + 0.5)) / n);
      e /= n;
      const drop = an.drops.some((d) => d >= t0 - 0.4 && d < t1 - 1);
      return { e, drop };
    }
    return { e: 0.45 + 0.5 * (0.5 + 0.5 * Math.sin(seg * 1.3 + 0.5)), drop: seg % 7 === 6 };
  }

  private pick(seg: number, mode: string): number {
    if (this.locked !== null) return this.locked;
    const seq = mode === 'music' ? this.seqTrack : this.seqFree;
    const s = Math.max(0, seg);
    while (seq.length <= s) {
      const i = seq.length;
      const { e, drop } = this.segInfo(i, mode);
      const rng = mulberry32(this.seed + i * 7919 + (mode === 'music' ? 0 : 99));
      const recent = seq.slice(-4);
      const weights = MOVES.map((m, idx) => {
        let w = m.w * (e >= m.lo && e <= m.hi ? 1 : 0.05);
        if (drop) w *= m.big ? 5 : 0.15;
        else if (m.rare) w *= 0.5;
        if (recent.includes(idx)) w *= 0.02;
        if (i === 0 && m.big) w *= 0.1;
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
      seq.push(chosen);
    }
    return seq[s];
  }

  update(m: MusicState, now: number): DirectorOut {
    const mode = m.mode;
    let target: Pose;
    let moveName: string;
    let nextName = '';
    const intensity = Math.min(1, Math.max(0, m.energy));

    if (mode === 'idle') {
      target = evalMove(IDLE, m.dancePos, { A: 0.6, e: 0.1 });
      moveName = IDLE.name;
    } else {
      const dp = m.dancePos;
      const seg = Math.floor(dp / SEG);
      const u = dp - seg * SEG;
      const A = 0.58 + 0.6 * intensity;
      const ctx = { A, e: intensity };
      const mv: Move = MOVES[this.pick(seg, mode)];
      target = evalMove(mv, u, ctx);
      moveName = mv.name;
      nextName = MOVES[this.pick(seg + 1, mode)].name;
      if (u < BLEND && seg >= 1) {
        const pv = MOVES[this.pick(seg - 1, mode)];
        const pp = evalMove(pv, u + SEG, ctx);
        target = lerpPose(pp, target, sm(u / BLEND));
      }
    }

    const lockKey = mode + ':' + this.locked;
    if (lockKey !== this.lastMode) {
      this.fromPose = this.lastPose;
      this.fromT = now;
      this.lastMode = lockKey;
    }
    const k = (now - this.fromT) / 700;
    let pose = k < 1 ? lerpPose(this.fromPose, target, sm(k)) : target;

    // react to the actual audio on top of the choreography
    if (mode === 'music') {
      const b = m.bass * m.bass;
      pose = { ...pose, hipH: pose.hipH - 0.02 * b, headY: pose.headY - 0.012 * b };
    }
    this.lastPose = pose;
    return { pose, moveName, nextName, intensity };
  }
}
