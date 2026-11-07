import { Pose } from './pose';
import { V3, add, app, ik2, mul, rx, ry, rz } from './math3d';

/**
 * Proportions (body units, whole figure ~1.0 tall).
 *
 * Modelled on the hand-drawn stickman references:
 *  - a BIG hollow head sitting directly on the body (there is NO neck)
 *  - one thick, uniform black stroke with round ends
 *  - the torso starts right at the rim of the head
 *  - the arms spring from the lower flanks of the head, short and softly curved
 *  - both legs leave from a single hip point (no pelvis bar), no hands, no feet
 */
const THIGH = 0.26;
const SHIN = 0.26;
export const TORSO = 0.24; // hip point -> rim of the head
const HIP_W = 0; // legs share one root, like the references
export const SH_W = 0.06; // arm roots sit on the lower flanks of the head
export const SH_DY = 0.008; // ...slightly above the base of the head
export const UPPER = 0.155;
export const FORE = 0.155;
export const HEAD_R = 0.122; // radius measured to the CENTRE of the stroke
export const LINE_W = 0.054; // thick marker stroke

// how much the elbow / knee corners are rounded (fraction of the bone length)
const ARM_ROUND = 0.36;
const LEG_ROUND = 0.2;

const CAM_PITCH = 0.2;
const CO = Math.cos(CAM_PITCH);
const SI = Math.sin(CAM_PITCH);
const D = Math.PI / 180;

export interface P2 { x: number; y: number; s: number }
export interface Cam { cx: number; fy: number; U: number }

export interface Rig {
  pelvis: P2; ctrl: P2; neck: P2;
  hipL: P2; hipR: P2; kneeL: P2; kneeR: P2; footL: P2; footR: P2;
  shL: P2; shR: P2; elL: P2; elR: P2; wrL: P2; wrR: P2;
  head: P2; headPx: number;
  anchor: P2; air: number;
}

/** The skeleton in world space (y up, +z toward the default viewer). */
export interface RigW {
  pelvis: V3; ctrl: V3; neck: V3;
  hipL: V3; hipR: V3; kneeL: V3; kneeR: V3; footL: V3; footR: V3;
  shL: V3; shR: V3; elL: V3; elR: V3; wrL: V3; wrR: V3;
  head: V3;
  anchor: V3; air: number;
}

const JOINTS = [
  'pelvis', 'ctrl', 'neck', 'hipL', 'hipR', 'kneeL', 'kneeR', 'footL', 'footR',
  'shL', 'shR', 'elL', 'elR', 'wrL', 'wrR', 'head', 'anchor',
] as const;

/** Mirror the skeleton in the floor plane (for the glossy reflection). */
export function mirrorRigW(r: RigW): RigW {
  const o = { ...r } as RigW;
  for (const k of JOINTS) {
    const v = r[k];
    o[k] = [v[0], -v[1], v[2]];
  }
  return o;
}


/** Is the bone a -> a+v (from fraction tmin on) clear of the head disc (chest-frame x/y)? */
function boneClear(a: V3, v: V3, hc: V3, R: number, tmin: number): boolean {
  for (let k = tmin; k <= 1.0001; k += 0.1) {
    if (Math.hypot(a[0] + v[0] * k - hc[0], a[1] + v[1] * k - hc[1]) < R) return false;
  }
  return true;
}

/** Rotate a bone (about z, as seen from the front) the smallest amount that clears the head. */
function clearBone(a: V3, v: V3, hc: V3, R: number, tmin: number, side: number): V3 {
  if (R < 0.02 || boneClear(a, v, hc, R, tmin)) return v;
  for (let step = 1; step <= 30; step++) {
    const th = step * 4 * D;
    const c = Math.cos(th), s = Math.sin(th);
    const v1: V3 = [v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2]];
    const v2: V3 = [v[0] * c + v[1] * s, -v[0] * s + v[1] * c, v[2]];
    const ok1 = boneClear(a, v1, hc, R, tmin), ok2 = boneClear(a, v2, hc, R, tmin);
    if (ok1 && ok2) return side * v1[0] >= side * v2[0] ? v1 : v2; // prefer swinging outward
    if (ok1) return v1;
    if (ok2) return v2;
  }
  return v;
}

const dirv = (side: number, a: number, f: number, l: number): V3 => {
  const A = a * D, F = f * D;
  return [side * Math.sin(A) * Math.cos(F) * l, -Math.cos(A) * Math.cos(F) * l, Math.sin(F) * l];
};

export function solveRigWorld(p: Pose): RigW {
  const Rp = mul(ry(p.hipYaw * D), rz(p.hipRoll * D));
  const Rmid = mul(Rp, mul(rz(p.spineRoll * 0.5 * D), rx(p.spinePitch * 0.5 * D)));
  const Rc = mul(Rp, mul(rz(p.spineRoll * D), mul(rx(p.spinePitch * D), ry(p.twist * D))));
  const Rh = mul(Rc, mul(rz(p.headRoll * D), rx(p.headPitch * D)));

  const P0: V3 = [p.px, p.hipH, p.pz];
  const hipL = add(P0, app(Rp, [-HIP_W, 0, 0]));
  const hipR = add(P0, app(Rp, [HIP_W, 0, 0]));
  const ctrl = add(P0, app(Rmid, [0, TORSO * 0.55, 0]));
  // "neck" = where the spine meets the base of the head (there is no real neck)
  const neck = add(add(P0, app(Rmid, [0, TORSO * 0.5, 0])), app(Rc, [0, TORSO * 0.5, 0]));
  const shL = add(neck, app(Rc, [-SH_W, SH_DY, 0]));
  const shR = add(neck, app(Rc, [SH_W, SH_DY, 0]));

  // --- arms (solved in the chest frame, wrapped around the big head) ---
  // The head is large and the arms spring from its flanks, so a raised arm would otherwise
  // slice straight through the hollow head. Bend the bone away from the head instead, the way
  // the "horn" arms hug the head in the references. `armClip` = 0 lets a pose cross the face on purpose.
  const hrot = mul(rz(p.headRoll * D), rx(p.headPitch * D));
  const hc = app(hrot, [p.headX, HEAD_R + p.headY, 0]); // head centre relative to the neck
  const clipK = Math.max(0, Math.min(1, p.armClip));
  const arm = (side: number, a1: number, f1: number, a2: number, f2: number) => {
    const sh: V3 = [side * SH_W, SH_DY, 0];
    const u = clearBone(sh, dirv(side, a1, f1, UPPER), hc, (HEAD_R + 0.004) * clipK, 0.5, side);
    const el: V3 = add(sh, u);
    const f = clearBone(el, dirv(side, a2, f2, FORE), hc, (HEAD_R + 0.03) * clipK, 0, side);
    return { el, wr: add(el, f) };
  };
  const aL = arm(-1, p.la1, p.lf1, p.la2, p.lf2);
  const aR = arm(1, p.ra1, p.rf1, p.ra2, p.rf2);
  const elL = add(neck, app(Rc, aL.el));
  const wrL = add(neck, app(Rc, aL.wr));
  const elR = add(neck, app(Rc, aR.el));
  const wrR = add(neck, app(Rc, aR.wr));

  // the head rests directly on the spine: its centre is one radius above the join
  const head = add(neck, app(Rh, [p.headX, HEAD_R + p.headY, 0]));

  const legL = ik2(hipL, [p.lfx, p.lfy, p.lfz], THIGH, SHIN, app(Rp, [-p.lko, 0, 1]));
  const legR = ik2(hipR, [p.rfx, p.rfy, p.rfz], THIGH, SHIN, app(Rp, [p.rko, 0, 1]));

  // global transform: yaw about the anchor, translate, roll about body centre
  const Ry = ry(p.yaw * D);
  const base = (v: V3): V3 => {
    const r = app(Ry, v);
    return [r[0] + p.x, r[1] + p.air, r[2]];
  };
  const C = base([P0[0], P0[1] + 0.14, P0[2]]);
  const rc = Math.cos(p.roll * D), rs = Math.sin(p.roll * D);
  const T = (v: V3): V3 => {
    const b = base(v);
    if (!p.roll) return b;
    const dx = b[0] - C[0], dy = b[1] - C[1];
    return [C[0] + rc * dx + rs * dy, C[1] - rs * dx + rc * dy, b[2]];
  };

  const q = T;
  return {
    pelvis: q(P0), ctrl: q(ctrl), neck: q(neck),
    hipL: q(hipL), hipR: q(hipR),
    kneeL: q(legL.mid), kneeR: q(legR.mid), footL: q(legL.end), footR: q(legR.end),
    shL: q(shL), shR: q(shR), elL: q(elL), elR: q(elR), wrL: q(wrL), wrR: q(wrR),
    head: q(head),
    anchor: [p.x, 0, 0], air: p.air,
  };
}

/** Classic fixed camera (slightly pitched, front-on). */
export function solveRig(p: Pose, cam: Cam): Rig {
  const w = solveRigWorld(p);
  const proj = (v: V3): P2 => {
    const yc = v[1] * CO - v[2] * SI;
    const zc = v[1] * SI + v[2] * CO;
    const s = 1 / (1 - zc * 0.16);
    return { x: cam.cx + v[0] * s * cam.U, y: cam.fy - yc * s * cam.U, s };
  };
  const headP = proj(w.head);
  return {
    pelvis: proj(w.pelvis), ctrl: proj(w.ctrl), neck: proj(w.neck),
    hipL: proj(w.hipL), hipR: proj(w.hipR),
    kneeL: proj(w.kneeL), kneeR: proj(w.kneeR), footL: proj(w.footL), footR: proj(w.footR),
    shL: proj(w.shL), shR: proj(w.shR), elL: proj(w.elL), elR: proj(w.elR), wrL: proj(w.wrL), wrR: proj(w.wrR),
    head: headP, headPx: HEAD_R * headP.s * cam.U,
    anchor: proj(w.anchor), air: w.air,
  };
}

/** Project a world-space rig with any projector that returns screen x/y and pixels-per-unit. */
export function projectRig(w: RigW, f: (v: V3) => { x: number; y: number; ppu: number }): Rig {
  const pr = (v: V3): P2 => {
    const r = f(v);
    return { x: r.x, y: r.y, s: r.ppu };
  };
  const headP = pr(w.head);
  return {
    pelvis: pr(w.pelvis), ctrl: pr(w.ctrl), neck: pr(w.neck),
    hipL: pr(w.hipL), hipR: pr(w.hipR),
    kneeL: pr(w.kneeL), kneeR: pr(w.kneeR), footL: pr(w.footL), footR: pr(w.footR),
    shL: pr(w.shL), shR: pr(w.shR), elL: pr(w.elL), elR: pr(w.elR), wrL: pr(w.wrL), wrR: pr(w.wrR),
    head: headP, headPx: HEAD_R * headP.s,
    anchor: pr(w.anchor), air: w.air,
  };
}

/**
 * A two-bone limb a -> b -> c drawn as one marker stroke whose corner at `b` is softly rounded,
 * like the bent-but-not-kinked arms and legs in the hand-drawn references.
 */
function limb(ctx: CanvasRenderingContext2D, a: P2, b: P2, c: P2, round: number) {
  ctx.moveTo(a.x, a.y);
  const ix = b.x + (a.x - b.x) * round, iy = b.y + (a.y - b.y) * round;
  const ox = b.x + (c.x - b.x) * round, oy = b.y + (c.y - b.y) * round;
  ctx.lineTo(ix, iy);
  ctx.quadraticCurveTo(b.x, b.y, ox, oy);
  ctx.lineTo(c.x, c.y);
}

export function drawRig(ctx: CanvasRenderingContext2D, r: Rig, U: number, color = '#000000') {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = Math.max(1.5, LINE_W * U);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  // Everything (incl. the head ring) is ONE path / ONE stroke, so overlaps merge into a clean
  // silhouette and the translucent floor reflection never double-darkens.
  ctx.beginPath();

  // legs: both leave from the same hip point, no feet
  limb(ctx, r.footL, r.kneeL, r.hipL, LEG_ROUND);
  limb(ctx, r.footR, r.kneeR, r.hipR, LEG_ROUND);

  // torso: one curved line from the hip point straight into the rim of the head (no neck)
  let vx = r.neck.x - r.head.x, vy = r.neck.y - r.head.y;
  const vl = Math.hypot(vx, vy);
  if (vl < 1e-4) {
    vx = 0; vy = 1;
  } else {
    vx /= vl; vy /= vl;
  }
  const rimX = r.head.x + vx * r.headPx, rimY = r.head.y + vy * r.headPx;
  ctx.moveTo(r.pelvis.x, r.pelvis.y);
  ctx.quadraticCurveTo(r.ctrl.x, r.ctrl.y, rimX, rimY);

  // arms: from the lower flanks of the head, no hands, no shoulder bar
  limb(ctx, r.wrL, r.elL, r.shL, ARM_ROUND);
  limb(ctx, r.wrR, r.elR, r.shR, ARM_ROUND);

  // the big hollow head (outline only)
  ctx.moveTo(r.head.x + r.headPx, r.head.y);
  ctx.arc(r.head.x, r.head.y, r.headPx, 0, Math.PI * 2);

  ctx.stroke();
  ctx.restore();
}
