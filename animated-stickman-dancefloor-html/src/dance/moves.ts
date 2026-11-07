import { Pose, mk, lerpPose } from './pose';

const TAU = Math.PI * 2;
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
export const sm = (x: number) => {
  const t = clamp01(x);
  return t * t * (3 - 2 * t);
};
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const fr = (x: number) => x - Math.floor(x);
/** sin with period `per` beats */
const sin = (b: number, per = 1, ph = 0) => Math.sin(TAU * (b / per + ph));
/** 1 on the beat, 0 on the off-beat */
const dip = (b: number) => 0.5 + 0.5 * Math.cos(TAU * b);
/** sharp hit on the beat that decays */
const hit = (b: number, k = 6) => Math.exp(-fr(b) * k);
/** looping keyframes over t in [0,1) with smooth easing between keys */
const kf = (t: number, keys: [number, number][]) => {
  const x = fr(t);
  for (let i = 0; i < keys.length - 1; i++) {
    const [t0, v0] = keys[i];
    const [t1, v1] = keys[i + 1];
    if (x >= t0 && x <= t1) return lerp(v0, v1, sm((x - t0) / (t1 - t0 || 1)));
  }
  return keys[keys.length - 1][1];
};
/** snap between a list of poses: fast pop on each slot, then hold */
const hold = (b: number, rate: number, list: Partial<Pose>[], snap = 5): Pose => {
  const x = b * rate;
  const n = list.length;
  const k = Math.floor(x);
  const t = sm(fr(x) * snap);
  const a = mk(list[(((k - 1) % n) + n) % n]);
  const c = mk(list[((k % n) + n) % n]);
  return lerpPose(a, c, t);
};


/** Mirror a (partial) pose left <-> right. */
const NEG = new Set(['px', 'headX', 'spineRoll', 'hipRoll', 'headRoll', 'twist', 'hipYaw']);
const mirrorPose = (p: Partial<Pose>): Partial<Pose> => {
  const o: Record<string, number> = {};
  for (const [k, v] of Object.entries(p) as [string, number][]) {
    if (/^[lr][af][12xyz]$/.test(k) || /^[lr]ko$/.test(k)) {
      const nk = (k[0] === 'l' ? 'r' : 'l') + k.slice(1);
      o[nk] = /^[lr]fx$/.test(k) ? -v : v;
    } else o[k] = NEG.has(k) ? -v : v;
  }
  return o as Partial<Pose>;
};
/** blend between a pose and its mirror image (t: 0 = as written, 1 = mirrored) */
const side = (p: Partial<Pose>, t: number): Pose => lerpPose(mk(p), mk(mirrorPose(p)), t);

// reference 5: the dab. one arm folded across the face, the other thrown out; the face hides in the elbow
const DAB: Partial<Pose> = {
  armClip: 0, la1: 138, lf1: 22, la2: -118, lf2: 38, ra1: 128, ra2: 128,
  headRoll: -24, headPitch: 28, headX: -0.03, spineRoll: -6, spinePitch: 8, hipH: 0.45, lfx: -0.12, rfx: 0.12,
};

// reference 1: the big sideways lean, one leg flung out, arms curling up round the head like horns
const LUNGE: Partial<Pose> = {
  hipH: 0.3, px: 0.05, lfx: -0.36, rfx: 0.14, lko: 0.6, rko: 1.3, spineRoll: 26, hipRoll: 6, headRoll: 8,
  la1: 158, la2: 172, ra1: 150, ra2: 165,
};

// reference 6: kneeling low, head heavy and forward, one hand resting on the hip
const KNEEL: Partial<Pose> = {
  hipH: 0.25, spinePitch: 18, headPitch: 10, lfx: -0.12, lfy: 0.02, lfz: -0.3, rfx: 0.16, rfz: 0.2,
  lko: 0.3, rko: 0.5, la1: 20, la2: 5, ra1: 75, ra2: -50,
};

export interface MoveCtx { A: number; e: number }
export interface Move {
  name: string;
  lo: number;
  hi: number;
  w: number;
  big?: boolean;
  rare?: boolean;
  fn: (b: number, c: MoveCtx) => Partial<Pose>;
}

// ---------- pose banks for pose-to-pose moves ----------
const ROBOT: Partial<Pose>[] = [
  { la1: 90, la2: 180, ra1: 90, ra2: 180 },
  { la1: 175, la2: 175, ra1: 20, ra2: 20, spineRoll: -8, headRoll: -12, px: -0.03 },
  { la1: 90, la2: 0, ra1: 90, ra2: 0, headX: 0.045, lfx: -0.17, rfx: 0.17, hipH: 0.44 },
  { la1: 20, la2: 20, ra1: 175, ra2: 175, spineRoll: 8, headRoll: 12, px: 0.03 },
  { la1: 60, la2: -55, ra1: 60, ra2: -55, headX: -0.045, hipH: 0.46 },
  { la1: 145, la2: 145, ra1: 145, ra2: 145, hipH: 0.43, lfx: -0.17, rfx: 0.17, headRoll: 0 },
  { la1: 90, la2: 90, ra1: 90, ra2: 0, twist: 22, headRoll: -10 },
  { la1: 90, la2: 0, ra1: 90, ra2: 90, twist: -22, headRoll: 10, headX: 0.04 },
];

const VOGUE: Partial<Pose>[] = [
  { la1: 120, la2: -150, ra1: 120, ra2: -150, hipH: 0.46, headRoll: 8, spineRoll: -4 },
  { la1: 165, la2: 165, ra1: 40, ra2: -100, hipRoll: 10, spineRoll: 10, px: -0.04, headRoll: -10, lfx: -0.16, rfx: 0.17, rfz: 0.1 },
  { la1: 90, la2: 180, ra1: 90, ra2: 0, hipRoll: -10, spineRoll: -8, px: 0.04, headRoll: 12, twist: 14, lfx: 0.02, lfz: 0.12 },
  { la1: 160, la2: 215, ra1: 160, ra2: 215, hipH: 0.42, lfx: -0.2, rfx: 0.2, lko: 1.6, rko: 1.6, headRoll: 0 },
  { la1: 40, la2: -100, ra1: 165, ra2: 165, hipRoll: -10, spineRoll: -10, px: 0.04, headRoll: 10, rfx: 0.02, rfz: 0.12, lfx: -0.17 },
  { la1: 125, la2: -30, ra1: 125, ra2: -30, hipH: 0.47, headRoll: -10, twist: -16 },
];

const YMCA: Partial<Pose>[] = [
  { la1: 150, la2: 150, ra1: 150, ra2: 150, hipH: 0.46, lfx: -0.13, rfx: 0.13 }, // Y
  { la1: 135, la2: -40, ra1: 135, ra2: -40, hipH: 0.45, headRoll: 6 }, // M
  { la1: 165, la2: 215, ra1: 30, ra2: -10, spineRoll: -10, hipRoll: 8, headRoll: 10, px: 0.03 }, // C
  { la1: 160, la2: 205, ra1: 160, ra2: 205, hipH: 0.44, headRoll: 0 }, // A
];

export const MOVES: Move[] = [
  {
    name: 'Groove Bounce', lo: 0, hi: 1, w: 1.5,
    fn: (b, { A }) => {
      const d = dip(b), sw = sin(b, 2);
      return {
        hipH: 0.46 - 0.05 * A * d, px: 0.045 * A * sw, hipRoll: 7 * A * sw, spineRoll: -6 * A * sw,
        headRoll: 6 * A * sw, twist: 8 * sin(b, 4), lfx: -0.11, rfx: 0.11,
        lfy: 0.025 * Math.max(0, sw), rfy: 0.025 * Math.max(0, -sw),
        la1: 38 + 10 * sw, lf1: 8, la2: 168 - 6 * sw, lf2: 12,
        ra1: 38 - 10 * sw, rf1: 8, ra2: 168 + 6 * sw, rf2: 12,
      };
    },
  },
  {
    name: 'Disco Point', lo: 0.25, hi: 0.9, w: 1.4,
    fn: (b, { A }) => {
      const sg = Math.tanh(3 * sin(b, 4));
      const pr = (1 + sg) / 2, pl = 1 - pr;
      const h = hit(b, 5);
      return {
        hipH: 0.44 - 0.035 * A * dip(b), px: -0.05 * sg, hipRoll: -9 * sg, spineRoll: 10 * sg,
        headRoll: -9 * sg, twist: -10 * sg, lfx: -0.17, rfx: 0.17, lko: 1.1, rko: 1.1,
        ra1: lerp(50, 148, pr) + 6 * h * pr, ra2: lerp(-35, 148, pr),
        la1: lerp(50, 148, pl) + 6 * h * pl, la2: lerp(-35, 148, pl),
        rf1: 0, rf2: 0, lf1: 0, lf2: 0,
      };
    },
  },
  {
    name: 'Running Man', lo: 0.45, hi: 1, w: 1.2,
    fn: (b, { A }) => {
      const s2 = sin(b, 2);
      const lL = Math.max(0, s2), lR = Math.max(0, -s2);
      const bob = Math.abs(Math.sin(Math.PI * b));
      return {
        air: 0.03 * A * bob, hipH: 0.45 - 0.02 * (1 - bob), spinePitch: 8, twist: 14 * s2, hipYaw: -8 * s2,
        lfy: 0.27 * Math.pow(lL, 0.7), lfz: 0.2 * lL - 0.05 * lR,
        rfy: 0.27 * Math.pow(lR, 0.7), rfz: 0.2 * lR - 0.05 * lL,
        la1: 35 - 22 * s2, lf1: -38 * s2, la2: 110 - 20 * s2, lf2: 40,
        ra1: 35 + 22 * s2, rf1: 38 * s2, ra2: 110 + 20 * s2, rf2: 40,
        x: 0.12 * sin(b, 8),
      };
    },
  },
  {
    name: 'Moonwalk', lo: 0.25, hi: 0.85, w: 1.1,
    fn: (b) => {
      const v = Math.sin((Math.PI * b) / 4);
      const foot = (off: number) => {
        const t = fr(b / 2 + off);
        if (t < 0.5) return { z: lerp(0.12, -0.12, t / 0.5), y: 0 };
        const s = sm((t - 0.5) / 0.5);
        return { z: lerp(-0.12, 0.12, s), y: 0.07 * Math.sin(Math.PI * s) };
      };
      const L = foot(0), R = foot(0.5);
      const sw = sin(b, 2);
      return {
        x: 0.5 * Math.cos((Math.PI * b) / 4), yaw: 78 * Math.tanh(3.5 * v),
        hipH: 0.455, spinePitch: -5, lfx: -0.06, rfx: 0.06,
        lfy: L.y, lfz: L.z, rfy: R.y, rfz: R.z,
        la1: 20, lf1: -34 * sw, la2: 25, lf2: 55 - 20 * sw,
        ra1: 20, rf1: 34 * sw, ra2: 25, rf2: 55 + 20 * sw,
        headRoll: 5 * sin(b, 4), spineRoll: 3 * sw,
      };
    },
  },
  {
    name: 'Pirouette', lo: 0.15, hi: 0.95, w: 1.2,
    fn: (b, { A }) => {
      const ph = fr(b / 4);
      const t = sm((ph - 0.28) / 0.44);
      const sp = sm(t * 6) * sm((1 - t) * 6);
      const c = kf(ph, [[0, 0], [0.18, 1], [0.28, 0.5], [0.4, 0], [0.8, 0], [0.9, 0.25], [1, 0]]);
      const a1 = kf(ph, [[0, 155], [0.15, 25], [0.28, 40], [0.4, 95], [0.62, 95], [0.75, 130], [0.85, 155], [1, 155]]);
      return {
        yaw: 360 * t, hipH: 0.48 - 0.1 * c + 0.015 * sp, air: 0.05 * sp * A, twist: -25 * c,
        px: -0.1 * sp, lfx: -0.09 - 0.0 * sp, rfx: 0.1 - 0.12 * sp, rfy: 0.25 * sp, rfz: 0.08 * sp, rko: 0.4 + 2.3 * sp,
        spineRoll: 4 * sp, headRoll: 5 * sin(b, 2),
        la1: a1, la2: a1 + 4, ra1: a1, ra2: a1 + 4,
      };
    },
  },
  {
    name: 'Robot', lo: 0.25, hi: 0.85, w: 1.0,
    fn: (b) => hold(b, 2, ROBOT, 7),
  },
  {
    name: 'Floss', lo: 0.4, hi: 1, w: 1.3,
    fn: (b, { A }) => {
      const s = Math.tanh(2.2 * sin(b, 2, 0.25)) / Math.tanh(2.2);
      const d = dip(b * 2);
      return {
        hipH: 0.43 - 0.025 * d, px: -0.1 * s, hipRoll: -13 * s * A, spineRoll: 7 * s, twist: -10 * s, headRoll: 8 * s,
        lfx: -0.13, rfx: 0.13, lko: 0.9, rko: 0.9,
        la1: -42 * s * A, lf1: 28, la2: -48 * s * A, lf2: 28,
        ra1: 42 * s * A, rf1: -26, ra2: 48 * s * A, rf2: -26,
      };
    },
  },
  {
    name: 'Arm Wave', lo: 0.05, hi: 0.75, w: 1.2,
    fn: (b, { A }) => {
      const w = (k: number) => Math.sin(TAU * (b / 2 - k));
      return {
        hipH: 0.45 - 0.025 * (0.5 + 0.5 * w(0)), px: 0.04 * w(0.05), hipRoll: -8 * w(0), spineRoll: 10 * w(0.12),
        headRoll: 12 * w(0.3), twist: 6 * w(0.2), lfx: -0.13, rfx: 0.13,
        la1: 100 + 32 * A * w(0), la2: 100 + 45 * A * w(0.1), lf1: 8 * w(0.05),
        ra1: 100 + 32 * A * w(0.28), ra2: 100 + 45 * A * w(0.38), rf1: 8 * w(0.33),
      };
    },
  },
  {
    name: 'Low Twist', lo: 0.4, hi: 1, w: 1.2,
    fn: (b, { A }) => {
      const t = sin(b, 1);
      const d = dip(b * 2);
      return {
        hipH: 0.35 - 0.03 * d, lfx: -0.21, rfx: 0.21, lko: 1.4, rko: 1.4,
        hipYaw: 36 * A * t, twist: -62 * A * t, px: 0.02 * t, headRoll: 6 * t,
        la1: 48 + 12 * t, la2: 162, lf1: 12, ra1: 48 - 12 * t, ra2: 162, rf1: 12,
      };
    },
  },
  {
    name: 'Hip-Hop Bounce', lo: 0.3, hi: 1, w: 1.4,
    fn: (b, { A }) => {
      const d = dip(b), s2 = sin(b, 2);
      return {
        hipH: 0.4 - 0.05 * A * d, spinePitch: 10 * d, headY: -0.02 * d, headRoll: 8 * s2,
        px: 0.06 * s2, hipRoll: 10 * s2, spineRoll: -8 * s2, lfx: -0.17, rfx: 0.17, lko: 1, rko: 1,
        la1: 55 - 45 * s2, la2: 45 - 45 * s2, lf1: 18, lf2: 25,
        ra1: 55 + 45 * s2, ra2: 45 + 45 * s2, rf1: 18, rf2: 25,
      };
    },
  },
  {
    name: 'Jumping Jacks', lo: 0.55, hi: 1, w: 1.0, big: true,
    fn: (b, { A }) => {
      const j = Math.sin(Math.PI * fr(b));
      const o = sm(0.5 - 0.5 * Math.cos(Math.PI * b));
      return {
        air: 0.15 * A * j, hipH: 0.48 - 0.07 * Math.pow(1 - j, 2),
        lfx: -(0.1 + 0.15 * o), rfx: 0.1 + 0.15 * o, lfy: 0.06 * j, rfy: 0.06 * j,
        la1: 15 + 140 * o, la2: 20 + 150 * o + 28 * o * o, ra1: 15 + 140 * o, ra2: 20 + 150 * o + 28 * o * o,
        headRoll: 4 * sin(b, 2),
      };
    },
  },
  {
    name: 'Pogo', lo: 0.55, hi: 1, w: 0.8, big: true,
    fn: (b, { A }) => {
      const j = Math.sin(Math.PI * fr(b));
      const s2 = sin(b, 2);
      return {
        air: 0.17 * A * j, hipH: 0.48 - 0.08 * Math.pow(1 - j, 2), lfy: 0.1 * j, rfy: 0.1 * j, lfz: 0.06 * j, rfz: 0.06 * j,
        la1: 150 + 18 * s2, la2: 160 + 14 * s2, ra1: 150 - 18 * s2, ra2: 160 - 14 * s2,
        headRoll: 10 * s2, spineRoll: 5 * s2,
      };
    },
  },
  {
    name: 'Side Kicks', lo: 0.5, hi: 1, w: 1.0,
    fn: (b, { A }) => {
      const s2 = sin(b, 2);
      const kL = Math.max(0, s2), kR = Math.max(0, -s2);
      return {
        px: 0.055 * (kL - kR), hipRoll: -9 * (kL - kR), spineRoll: 6 * (kL - kR), hipH: 0.47 - 0.02 * dip(b),
        lfx: -0.1 - 0.27 * kL, lfy: 0.24 * kL * A, lfz: 0.06 * kL,
        rfx: 0.1 + 0.27 * kR, rfy: 0.24 * kR * A, rfz: 0.06 * kR,
        ra1: 30 + 105 * kL, ra2: 38 + 112 * kL, la1: 30 + 105 * kR, la2: 38 + 112 * kR,
        headRoll: 6 * (kL - kR),
      };
    },
  },
  {
    name: 'Sprinkler', lo: 0.3, hi: 0.85, w: 1.0,
    fn: (b) => {
      const k = Math.floor(fr(b / 2) * 5) / 4;
      const kk = fr(b / 2) * 5 - Math.floor(fr(b / 2) * 5);
      const f = 80 * (k + 0.25 * sm(kk * 6)) * (k >= 1 ? 0 : 1);
      const d = dip(b);
      return {
        hipH: 0.45 - 0.035 * d, lfx: -0.12, rfx: 0.12,
        ra1: 92, rf1: Math.min(80, f), ra2: 92, rf2: Math.min(80, f),
        la1: 118, la2: -140, lf1: 0, lf2: 0,
        twist: -0.2 * f, headRoll: 6 * sin(b, 2) - 4, spineRoll: 4 * sin(b, 2),
      };
    },
  },
  {
    name: 'Vogue', lo: 0.2, hi: 0.85, w: 1.0,
    fn: (b) => hold(b, 1, VOGUE, 6),
  },
  {
    name: 'Headbang', lo: 0.7, hi: 1, w: 1.0, big: true,
    fn: (b, { A }) => {
      const d = dip(b);
      return {
        hipH: 0.42 - 0.04 * d, spinePitch: 8 + 32 * d * A, headPitch: 25 * d, headY: -0.03 * d,
        headRoll: 12 * sin(b, 2), twist: 8 * sin(b, 2), lfx: -0.19, rfx: 0.19, lko: 1.2, rko: 1.2,
        ra1: 125 + 30 * d, ra2: 150 + 20 * d, la1: 112 - 10 * d, la2: 150, rf1: 10, lf1: 10,
      };
    },
  },
  {
    name: 'Star Cartwheel', lo: 0.7, hi: 1, w: 0.55, big: true, rare: true,
    fn: (b) => {
      const cyc = Math.floor(b / 4);
      const dir = cyc % 2 === 0 ? 1 : -1;
      const ph = fr(b / 4);
      const t = sm((ph - 0.25) / 0.5);
      const crouch = ph < 0.28 ? sm(ph / 0.2) * (1 - sm((ph - 0.2) / 0.08)) : 0;
      const ao = kf(ph, [[0, 0.3], [0.2, 0], [0.27, 0.4], [0.32, 1], [0.72, 1], [0.8, 0.9], [0.95, 0.5], [1, 0.3]]);
      return {
        roll: dir * 360 * t, x: dir * (-0.62 + 1.24 * t), air: 0.1 * Math.sin(Math.PI * t), hipH: 0.48 - 0.1 * crouch,
        la1: lerp(25, 158, ao), la2: lerp(30, 160, ao), ra1: lerp(25, 158, ao), ra2: lerp(30, 160, ao),
        lfx: -(0.1 + 0.2 * ao), rfx: 0.1 + 0.2 * ao, spineRoll: -dir * 6 * crouch,
      };
    },
  },
  {
    name: 'Tuck Flip', lo: 0.8, hi: 1, w: 0.45, big: true, rare: true,
    fn: (b) => {
      const ph = fr(b / 4);
      const t = sm((ph - 0.3) / 0.4);
      const dir = Math.floor(b / 4) % 2 === 0 ? 1 : -1;
      const crouch = ph < 0.32 ? sm(ph / 0.22) * (1 - sm((ph - 0.22) / 0.1)) : 0;
      const tuck = sm(t * 5) * sm((1 - t) * 5);
      const ao = kf(ph, [[0, 0.5], [0.2, 0], [0.3, 0.6], [0.4, 1], [0.7, 1], [0.8, 0.8], [1, 0.5]]);
      return {
        roll: dir * 360 * t, air: 0.62 * Math.sin(Math.PI * t), x: dir * (-0.3 + 0.6 * t), hipH: 0.48 - 0.1 * crouch,
        lfy: 0.3 * tuck, rfy: 0.3 * tuck, lfz: 0.2 * tuck, rfz: 0.2 * tuck, lfx: -0.1 + 0.03 * tuck, rfx: 0.1 - 0.03 * tuck,
        la1: lerp(25, 150, ao) * (1 - tuck) + 35 * tuck, ra1: lerp(25, 150, ao) * (1 - tuck) + 35 * tuck,
        la2: lerp(30, 155, ao) * (1 - tuck) + 20 * tuck, ra2: lerp(30, 155, ao) * (1 - tuck) + 20 * tuck,
        lf1: 40 * tuck, rf1: 40 * tuck, lf2: 20 + 70 * tuck, rf2: 20 + 70 * tuck,
        spinePitch: 25 * tuck,
      };
    },
  },
  {
    name: 'Shuffle', lo: 0.5, hi: 1, w: 1.1,
    fn: (b, { A }) => {
      const o = sm(0.5 + 0.5 * sin(b, 1));
      return {
        x: 0.4 * sin(b, 8), hipH: 0.455 - 0.03 * dip(b * 2), spinePitch: 6,
        lfx: -0.08 - 0.17 * o, rfx: 0.08 + 0.17 * (1 - o), lko: 0.7, rko: 0.7,
        hipRoll: 8 * (o - 0.5) * A, spineRoll: -6 * (o - 0.5), twist: 14 * (o - 0.5), headRoll: 6 * (o - 0.5),
        la1: 36 + 26 * o, la2: 130 - 30 * o, lf1: 18, lf2: 40, ra1: 36 + 26 * (1 - o), ra2: 130 - 30 * (1 - o), rf1: 18, rf2: 40,
      };
    },
  },
  {
    name: 'Hands Up Clap', lo: 0.5, hi: 1, w: 1.0, big: true,
    fn: (b, { A }) => {
      const o = sm(0.5 + 0.5 * Math.cos(TAU * b));
      const h = hit(b, 7);
      return {
        air: 0.045 * A * h, hipH: 0.46 - 0.04 * dip(b), hipRoll: 6 * sin(b, 2), spineRoll: -4 * sin(b, 2), headRoll: 6 * sin(b, 2),
        lfx: -0.13, rfx: 0.13,
        la1: 148 + 8 * o, la2: lerp(148, 206, o), ra1: 148 + 8 * o, ra2: lerp(148, 206, o),
      };
    },
  },
  {
    name: 'Body Roll', lo: 0, hi: 0.7, w: 1.2,
    fn: (b) => {
      const w = TAU * (b / 2);
      return {
        px: 0.07 * Math.cos(w), pz: 0.07 * Math.sin(w), hipYaw: 18 * Math.cos(w - 0.6), hipRoll: 10 * Math.cos(w - 0.3),
        spineRoll: -12 * Math.cos(w - 1.1), spinePitch: 8 * Math.sin(w - 0.8), twist: -14 * Math.cos(w - 0.6),
        headRoll: 9 * Math.cos(w - 1.7), headX: 0.02 * Math.cos(w - 1.7), hipH: 0.44 - 0.03 * Math.sin(2 * w),
        lfx: -0.13, rfx: 0.13, lko: 0.9, rko: 0.9,
        la1: 140 + 22 * Math.sin(w), la2: 150 + 30 * Math.sin(w - 0.9), lf1: 12 * Math.cos(w),
        ra1: 140 + 22 * Math.sin(w + 2.2), ra2: 150 + 30 * Math.sin(w + 1.3), rf1: 12 * Math.cos(w + 2.2),
      };
    },
  },
  {
    name: 'Lasso', lo: 0.35, hi: 1, w: 1.0,
    fn: (b) => {
      const th = TAU * b;
      return {
        hipH: 0.44 - 0.03 * dip(b), lfx: -0.15, rfx: 0.15, lko: 1, rko: 1, hipRoll: 6, spineRoll: -7 + 4 * sin(b, 2),
        px: 0.03 * sin(b, 2), headRoll: 6 * sin(b, 2),
        la1: 150 + 22 * Math.cos(th), lf1: 45 * Math.sin(th), la2: 150 + 35 * Math.cos(th - 0.7), lf2: 60 * Math.sin(th - 0.7),
        ra1: 52, ra2: -38,
      };
    },
  },
  {
    name: 'Y-M-C-A', lo: 0.35, hi: 0.95, w: 0.9,
    fn: (b) => hold(b, 1, YMCA, 6),
  },
  {
    name: 'Step Touch', lo: 0, hi: 0.55, w: 1.3,
    fn: (b) => {
      const sw = sin(b, 2);
      const d = dip(b);
      return {
        x: 0.18 * sin(b, 4), px: 0.07 * sw, hipH: 0.47 - 0.03 * d, hipRoll: 5 * sw, spineRoll: -4 * sw, headRoll: 4 * sw,
        lfy: 0.05 * Math.max(0, sw), rfy: 0.05 * Math.max(0, -sw), lfx: -0.11, rfx: 0.11,
        la1: 28 + 22 * sw, la2: 40 + 30 * sw, lf1: 10, lf2: 40, ra1: 28 - 22 * sw, ra2: 40 - 30 * sw, rf1: 10, rf2: 40,
      };
    },
  },
  {
    name: 'Dab', lo: 0.4, hi: 1, w: 1.0,
    fn: (b, { A }) => {
      const ph = fr(b / 2);
      const on = sm(ph * 10) * (1 - sm((ph - 0.72) * 8));
      const flip = Math.floor(b / 2) % 2 === 0 ? 0 : 1;
      const rest: Partial<Pose> = {
        hipH: 0.46 - 0.04 * A * dip(b), lfx: -0.12, rfx: 0.12, headRoll: 5 * sin(b, 2),
        la1: 36, la2: 150, ra1: 36, ra2: 150,
      };
      return lerpPose(mk(rest), side(DAB, flip), on);
    },
  },
  {
    name: 'Lunge Lean', lo: 0.35, hi: 1, w: 1.0,
    fn: (b, { A }) => {
      const t = sm(0.5 + 0.5 * Math.tanh(3 * sin(b, 4)));
      const p = side({ ...LUNGE, hipH: 0.3 - 0.025 * A * dip(b * 2), headRoll: 8 + 4 * sin(b, 1) }, t);
      return p;
    },
  },
  {
    name: 'Kneel Rest', lo: 0, hi: 0.5, w: 0.8,
    fn: (b) => {
      const t = sm(0.5 + 0.5 * Math.tanh(3 * sin(b, 8)));
      return side({ ...KNEEL, hipH: 0.25 + 0.006 * sin(b, 2), headRoll: 8 * sin(b, 4), spinePitch: 18 + 3 * sin(b, 2) }, t);
    },
  },
  {
    name: 'Sprint', lo: 0.6, hi: 1, w: 0.9,
    fn: (b, { A }) => {
      const th = TAU * (b / 1);
      const sL = Math.sin(th), cL = Math.cos(th);
      const sR = -sL, cR = -cL;
      const bob = Math.abs(Math.sin(th));
      return {
        yaw: 74, air: 0.04 * A * bob, hipH: 0.45 - 0.02 * (1 - bob), spinePitch: 24, headPitch: -6,
        lfy: 0.24 * Math.max(0, cL), lfz: 0.3 * sL, rfy: 0.24 * Math.max(0, cR), rfz: 0.3 * sR,
        lfx: -0.04, rfx: 0.04, lko: 0, rko: 0,
        la1: 50, lf1: -48 * sR, la2: 95, lf2: 55 - 20 * sR,
        ra1: 50, rf1: -48 * sL, ra2: 95, rf2: 55 - 20 * sL,
        x: 0.25 * sin(b, 8), hipYaw: 6 * sL, armClip: 0.6,
      };
    },
  },
];

export const IDLE: Move = {
  name: 'Waiting for the beat…', lo: 0, hi: 1, w: 1,
  fn: (b) => ({
    hipH: 0.476 + 0.004 * sin(b, 2), spineRoll: 1.5 * sin(b, 4), headRoll: 2 * sin(b, 8),
    la1: 48 + 3 * sin(b, 2), ra1: 48 + 3 * sin(b, 2, 0.3), la2: 4 + 3 * sin(b, 4), ra2: 4 + 3 * sin(b, 4, 0.4),
  }),
};

export const evalMove = (m: Move, b: number, ctx: MoveCtx): Pose => mk(m.fn(b, ctx));
