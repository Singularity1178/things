/**
 * Exact (continuous) "keep this bone out of the head disc" solver, used by the smooth rig.
 *
 * The classic solver rotates the bone in coarse 4° steps and samples it every 10% of its length, so the
 * result moves in visible little jumps. This one tests the bone against the disc exactly (closest point on
 * the segment) and bisects to the precise tangent angle, so the output changes smoothly with the input.
 */

const DEG = Math.PI / 180;

/** Is the segment a + v*k, k in [tmin, 1], touching the disc (h, R)? */
function blocked(ax: number, ay: number, vx: number, vy: number, hx: number, hy: number, R: number, tmin: number): boolean {
  const sx = ax + vx * tmin, sy = ay + vy * tmin;
  const ex = vx * (1 - tmin), ey = vy * (1 - tmin);
  const l2 = ex * ex + ey * ey;
  let k = l2 > 1e-12 ? ((hx - sx) * ex + (hy - sy) * ey) / l2 : 0;
  k = k < 0 ? 0 : k > 1 ? 1 : k;
  const dx = sx + ex * k - hx, dy = sy + ey * k - hy;
  return dx * dx + dy * dy < R * R;
}

/** Smallest rotation (radians, >0) in direction `dir` (+1 / -1) that frees the bone, or Infinity. */
function firstClear(
  ax: number, ay: number, vx: number, vy: number, hx: number, hy: number, R: number, tmin: number, dir: number,
): number {
  const STEP = 2 * DEG;
  for (let th = STEP; th <= Math.PI + 1e-9; th += STEP) {
    const c = Math.cos(th), s = Math.sin(th) * dir;
    if (!blocked(ax, ay, vx * c - vy * s, vx * s + vy * c, hx, hy, R, tmin)) {
      let lo = th - STEP, hi = th;
      for (let i = 0; i < 22; i++) {
        const mid = (lo + hi) / 2;
        const cm = Math.cos(mid), sm = Math.sin(mid) * dir;
        if (blocked(ax, ay, vx * cm - vy * sm, vx * sm + vy * cm, hx, hy, R, tmin)) lo = mid;
        else hi = mid;
      }
      return hi;
    }
  }
  return Infinity;
}

/**
 * Signed rotation (radians; positive = the usual [c -s; s c] turn) that moves the bone a -> a+v clear of the
 * disc. 0 when it is already clear. `biasPos` / `biasNeg` (radians) make one swing direction look cheaper,
 * which is how callers express "prefer outward" or "stay on the side you were on" (hysteresis).
 */
export function clearRotation(
  ax: number, ay: number, vx: number, vy: number, hx: number, hy: number, R: number, tmin: number,
  biasPos = 0, biasNeg = 0,
): number {
  if (!blocked(ax, ay, vx, vy, hx, hy, R, tmin)) return 0;
  const p = firstClear(ax, ay, vx, vy, hx, hy, R, tmin, 1);
  const n = firstClear(ax, ay, vx, vy, hx, hy, R, tmin, -1);
  if (p === Infinity && n === Infinity) return 0;
  return p - biasPos <= n - biasNeg ? p : -n;
}
