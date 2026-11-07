export type V3 = [number, number, number];
export type M3 = [number, number, number, number, number, number, number, number, number];

// y is UP, z points toward the viewer. Positive roll = clockwise on screen.
export const rx = (a: number): M3 => {
  const c = Math.cos(a), s = Math.sin(a);
  return [1, 0, 0, 0, c, -s, 0, s, c];
};
export const ry = (a: number): M3 => {
  const c = Math.cos(a), s = Math.sin(a);
  return [c, 0, s, 0, 1, 0, -s, 0, c];
};
export const rz = (a: number): M3 => {
  const c = Math.cos(a), s = Math.sin(a);
  return [c, s, 0, -s, c, 0, 0, 0, 1];
};

export const mul = (a: M3, b: M3): M3 => {
  const r = new Array(9) as M3;
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      r[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j];
    }
  }
  return r;
};

export const app = (m: M3, v: V3): V3 => [
  m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
  m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
  m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
];

export const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const scl = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
export const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);

/** Two-bone IK. Returns knee and the (possibly clamped) end point. */
export function ik2(root: V3, target: V3, l1: number, l2: number, pole: V3): { mid: V3; end: V3 } {
  const d = sub(target, root);
  const dist = len(d);
  const maxD = l1 + l2 - 1e-4;
  const minD = Math.abs(l1 - l2) + 1e-3;
  const dc = Math.min(maxD, Math.max(minD, dist));
  const n: V3 = dist > 1e-6 ? scl(d, 1 / dist) : [0, -1, 0];
  const a = (l1 * l1 - l2 * l2 + dc * dc) / (2 * dc);
  const h = Math.sqrt(Math.max(0, l1 * l1 - a * a));
  let p = sub(pole, scl(n, dot(pole, n)));
  const pl = len(p);
  p = pl < 1e-4 ? [0, 0, 1] : scl(p, 1 / pl);
  const mid = add(add(root, scl(n, a)), scl(p, h));
  const end = add(root, scl(n, dc));
  return { mid, end };
}
