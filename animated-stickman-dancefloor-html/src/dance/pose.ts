/**
 * A Pose is a flat bag of numbers describing the whole body.
 * Angles are degrees, lengths are in "body units" (the figure is ~1.0 tall).
 *
 * Arms (per side): a = angle from hanging-down in the frontal plane (0 = down, 90 = out sideways,
 * 180 = straight up, negative = across the body); f = forward swing (toward the viewer).
 * 1 = upper arm, 2 = forearm (absolute directions, not relative).
 *
 * Feet are IK targets relative to the floor anchor, so feet stay planted while the pelvis moves.
 */
export interface Pose {
  x: number; // whole body lateral position
  px: number; // pelvis offset relative to the feet anchor
  pz: number; // pelvis depth offset
  air: number; // jump height
  yaw: number; // spin around vertical axis
  roll: number; // rotation in the screen plane (cartwheels / flips)
  hipH: number; // pelvis height (0.48 = standing)
  hipYaw: number;
  hipRoll: number;
  spineRoll: number;
  spinePitch: number;
  twist: number; // chest twist relative to hips
  headRoll: number;
  headPitch: number;
  headX: number;
  headY: number;
  la1: number; lf1: number; la2: number; lf2: number;
  ra1: number; rf1: number; ra2: number; rf2: number;
  lfx: number; lfy: number; lfz: number;
  rfx: number; rfy: number; rfz: number;
  lko: number; rko: number; // knee-out amount
  armClip: number; // 1 = arms wrap around the head, 0 = arms may cross the face (dab!)
}

export const BASE: Pose = {
  x: 0, px: 0, pz: 0, air: 0, yaw: 0, roll: 0, hipH: 0.48,
  hipYaw: 0, hipRoll: 0, spineRoll: 0, spinePitch: 0, twist: 0,
  headRoll: 0, headPitch: 0, headX: 0, headY: 0,
  la1: 50, lf1: 0, la2: 4, lf2: 8,
  ra1: 50, rf1: 0, ra2: 4, rf2: 8,
  lfx: -0.1, lfy: 0, lfz: 0, rfx: 0.1, rfy: 0, rfz: 0,
  lko: 0.4, rko: 0.4, armClip: 1,
};

export const KEYS = Object.keys(BASE) as (keyof Pose)[];
const WRAP = new Set<keyof Pose>(['yaw', 'roll']);

export const mk = (o: Partial<Pose>): Pose => ({ ...BASE, ...o });

export function lerpPose(a: Pose, b: Pose, t: number): Pose {
  const r = { ...BASE } as Pose;
  for (const k of KEYS) {
    if (WRAP.has(k)) {
      const d = ((((b[k] - a[k]) % 360) + 540) % 360) - 180;
      r[k] = a[k] + d * t;
    } else {
      r[k] = a[k] + (b[k] - a[k]) * t;
    }
  }
  return r;
}
