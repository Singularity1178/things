import { clearRotation } from './clearance';
import type { P2, Rig } from './rig';

/**
 * Smooth screen-space anchoring for the whole figure (arms AND body).
 *
 * The classic anchoring re-solves everything from scratch every frame, so every tiny numerical
 * discontinuity (stepped searches, left/right flips, a shoulder passing in front of the head centre)
 * shows up on screen as jitter. This version:
 *   1. solves the clearance exactly and with hysteresis (see clearance.ts), then
 *   2. low-passes the *head-relative* shape (ring angle, bone angles, bone lengths) with a speed-adaptive
 *      One-Euro filter - heavy smoothing when things are nearly still, almost none during fast moves, and
 *      because it only filters relative quantities the figure never lags behind the head.
 *
 * The body gets the same treatment as the arms: the torso is pinned to the head ring at a filtered angle
 * and its curve is kept out of the hollow head, and both legs are pinned to the torso's hip point.
 */

const TAU = Math.PI * 2;
const DEG = Math.PI / 180;
const wrap = (a: number) => a - TAU * Math.round(a / TAU);
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const smooth01 = (x: number) => {
  const t = clamp01(x);
  return t * t * (3 - 2 * t);
};

/** One-Euro filter: adaptive low-pass (Casiez et al. 2012). */
class OneEuro {
  private x: number | null = null;
  private dx = 0;
  constructor(private minCut: number, private beta: number, private maxRate = Infinity, private dCut = 2) {}
  reset() {
    this.x = null;
    this.dx = 0;
  }
  get value(): number | null {
    return this.x;
  }
  private alpha(fc: number, dt: number) {
    return 1 / (1 + 1 / (TAU * fc) / dt);
  }
  step(v: number, dt: number, angular = false): number {
    if (this.x === null) {
      this.x = v;
      this.dx = 0;
      return v;
    }
    if (angular) v = this.x + wrap(v - this.x);
    this.dx += this.alpha(this.dCut, dt) * ((v - this.x) / dt - this.dx);
    const fc = this.minCut + this.beta * Math.abs(this.dx);
    let step = this.alpha(fc, dt) * (v - this.x);
    // slew limit: a discontinuity in the input (e.g. the arm choosing the other side of the head)
    // becomes a short swing instead of a one-frame pop
    const cap = this.maxRate * dt;
    if (step > cap) step = cap;
    else if (step < -cap) step = -cap;
    this.x += step;
    return this.x;
  }
}

/** Filters a 2D vector as (angle, length) so smoothing never shrinks it while it rotates. */
class PolarFilter {
  private a = new OneEuro(4, 0.9, MAX_SPIN);
  private l = new OneEuro(4, 2);
  reset() {
    this.a.reset();
    this.l.reset();
  }
  step(vx: number, vy: number, dt: number): [number, number] {
    const ang = this.a.step(Math.atan2(vy, vx), dt, true);
    const len = this.l.step(Math.hypot(vx, vy), dt);
    return [Math.cos(ang) * len, Math.sin(ang) * len];
  }
}

class ArmState {
  ring = new OneEuro(3.5, 0.9, MAX_SPIN);
  up = new PolarFilter();
  fore = new PolarFilter();
  sUp = 0; // remembered swing direction (hysteresis)
  sFore = 0;
  reset() {
    this.ring.reset();
    this.up.reset();
    this.fore.reset();
    this.sUp = 0;
    this.sFore = 0;
  }
}

/** How close to the head centre a root may get before it blends toward the spine direction (in head radii). */
const SOFT = 0.5;
/** Fastest an arm / ring angle may turn on screen (rad/s) */
const MAX_SPIN = 24;
const HYST = 45 * DEG; // how much better the other swing direction must be before we switch to it

/**
 * Angle on the head ring where a limb root should sit, as seen from the camera.
 *
 * The camera's view of the limb root (its direction from the head centre) is only trustworthy while the
 * root is a decent distance from the head centre. When the camera looks down the body, shoulders / neck
 * stack right on top of the head and that direction spins wildly. So the further inside `soft` we get,
 * the less we follow it and the more we simply HOLD the last good angle.
 */
function ringTarget(f: OneEuro, from: P2, head: P2, fallback: number, soft: number): number {
  const dx = from.x - head.x, dy = from.y - head.y;
  const len = Math.hypot(dx, dy);
  const cur = f.value;
  if (cur === null) {
    // first frame: nothing to hold, lean on the spine direction
    return len < 1e-4 ? fallback : fallback + wrap(Math.atan2(dy, dx) - fallback) * smooth01(len / soft);
  }
  if (len < 1e-4) return cur;
  const t = smooth01(len / soft);
  return cur + wrap(Math.atan2(dy, dx) - cur) * t * t;
}

export class Anchorer {
  /** seconds since the previous frame (set by the caller once per frame) */
  dt = 1 / 60;
  private L = new ArmState();
  private R = new ArmState();
  private torsoRing = new OneEuro(3.5, 0.9, MAX_SPIN);
  private torsoCtrl = new PolarFilter();
  private prev: { x: number; y: number; px: number } | null = null;

  /** Forget all smoothing state (camera cut, mode change...). */
  reset() {
    this.L.reset();
    this.R.reset();
    this.torsoRing.reset();
    this.torsoCtrl.reset();
    this.prev = null;
  }

  apply(r: Rig, clip: number): Rig {
    const dt = Math.min(0.1, Math.max(1 / 240, this.dt));
    const { head, headPx } = r;

    // a hard cut (new camera shot, different scale) must not be smoothed across
    const p = this.prev;
    if (p) {
      const ratio = headPx / Math.max(1e-6, p.px);
      if (Math.hypot(head.x - p.x, head.y - p.y) > 2.5 * Math.max(headPx, p.px) || ratio > 1.6 || ratio < 0.62) this.reset();
    }
    this.prev = { x: head.x, y: head.y, px: headPx };

    // screen-space "down the spine" direction (fallback for limbs whose root is right over the head centre)
    const spine = Math.hypot(r.neck.x - head.x, r.neck.y - head.y) > 1e-4
      ? Math.atan2(r.neck.y - head.y, r.neck.x - head.x)
      : Math.PI / 2;

    const L = this.arm(this.L, r.shL, r.elL, r.wrL, r, spine, clip, dt);
    const Rr = this.arm(this.R, r.shR, r.elR, r.wrR, r, spine, clip, dt);
    const body = this.torso(r, dt);

    // legs leave from the very same hip point the torso ends on
    const hip: P2 = { ...r.pelvis };
    return {
      ...r,
      shL: L.root, elL: L.el, wrL: L.wr,
      shR: Rr.root, elR: Rr.el, wrR: Rr.wr,
      hipL: { ...hip }, hipR: { ...hip },
      body,
    };
  }

  private arm(st: ArmState, sh: P2, el: P2, wr: P2, r: Rig, spine: number, clip: number, dt: number) {
    const { head, headPx } = r;

    // where the camera sees this shoulder relative to the head -> spot on the ring
    const th = st.ring.step(ringTarget(st.ring, sh, head, spine, headPx * SOFT), dt, true);
    const root: P2 = { x: head.x + Math.cos(th) * headPx, y: head.y + Math.sin(th) * headPx, s: sh.s };

    // the arm keeps its own shape and just travels with its root
    let ux = el.x - sh.x, uy = el.y - sh.y;
    let fx = wr.x - el.x, fy = wr.y - el.y;

    // wrap around the hollow head instead of cutting through it
    const R = headPx * clamp01(clip) * 0.97;
    if (R > headPx * 0.05) {
      const t1 = clearRotation(root.x, root.y, ux, uy, head.x, head.y, R, 0.1, st.sUp > 0 ? HYST : 0, st.sUp < 0 ? HYST : 0);
      st.sUp = Math.sign(t1);
      if (t1) {
        const c = Math.cos(t1), s = Math.sin(t1);
        [ux, uy] = [ux * c - uy * s, ux * s + uy * c];
        [fx, fy] = [fx * c - fy * s, fx * s + fy * c];
      }
      const ex = root.x + ux, ey = root.y + uy;
      const t2 = clearRotation(ex, ey, fx, fy, head.x, head.y, R, 0, st.sFore > 0 ? HYST : 0, st.sFore < 0 ? HYST : 0);
      st.sFore = Math.sign(t2);
      if (t2) {
        const c = Math.cos(t2), s = Math.sin(t2);
        [fx, fy] = [fx * c - fy * s, fx * s + fy * c];
      }
    } else {
      st.sUp = 0;
      st.sFore = 0;
    }

    // smooth the head-relative shape (normalised by the head size so zooming never matters)
    const [su, sv] = st.up.step(ux / headPx, uy / headPx, dt);
    const [sf, sg] = st.fore.step(fx / headPx, fy / headPx, dt);
    const e: P2 = { x: root.x + su * headPx, y: root.y + sv * headPx, s: el.s };
    const w: P2 = { x: e.x + sf * headPx, y: e.y + sg * headPx, s: wr.s };
    return { root, el: e, wr: w };
  }

  /** Pin the torso to the head ring and keep its curve out of the hollow head. */
  private torso(r: Rig, dt: number) {
    const { head, headPx, pelvis } = r;
    const toPelvis = Math.hypot(pelvis.x - head.x, pelvis.y - head.y) > 1e-4
      ? Math.atan2(pelvis.y - head.y, pelvis.x - head.x)
      : Math.PI / 2;
    const th = this.torsoRing.step(ringTarget(this.torsoRing, r.neck, head, toPelvis, headPx * SOFT), dt, true);
    const rim: P2 = { x: head.x + Math.cos(th) * headPx, y: head.y + Math.sin(th) * headPx, s: head.s };

    const [cx, cy] = this.torsoCtrl.step((r.ctrl.x - pelvis.x) / headPx, (r.ctrl.y - pelvis.y) / headPx, dt);
    let kx = pelvis.x + cx * headPx, ky = pelvis.y + cy * headPx;
    // the curve must arrive at the ring from outside, never from inside the head
    const minD = headPx * 1.25;
    let dx = kx - head.x, dy = ky - head.y;
    const d = Math.hypot(dx, dy);
    if (d < minD) {
      if (d < 1e-4) { dx = Math.cos(th); dy = Math.sin(th); } else { dx /= d; dy /= d; }
      kx = head.x + dx * minD;
      ky = head.y + dy * minD;
    }
    return { rim, ctrl: { x: kx, y: ky, s: r.ctrl.s } as P2 };
  }
}
