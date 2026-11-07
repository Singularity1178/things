import { Pose } from './pose';
import { V3, add, app, ik2, mul, rx, ry, rz } from './math3d';

// Proportions (body units, whole figure ~1.0 tall)
const THIGH = 0.26;
const SHIN = 0.26;
const TORSO = 0.31;
const HIP_W = 0.06;
const SH_W = 0.105;
const UPPER = 0.175;
const FORE = 0.175;
const NECK = 0.03;
export const HEAD_R = 0.085;
export const LINE_W = 0.03;

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

const dirv = (side: number, a: number, f: number, l: number): V3 => {
  const A = a * D, F = f * D;
  return [side * Math.sin(A) * Math.cos(F) * l, -Math.cos(A) * Math.cos(F) * l, Math.sin(F) * l];
};

export function solveRig(p: Pose, cam: Cam): Rig {
  const Rp = mul(ry(p.hipYaw * D), rz(p.hipRoll * D));
  const Rmid = mul(Rp, mul(rz(p.spineRoll * 0.5 * D), rx(p.spinePitch * 0.5 * D)));
  const Rc = mul(Rp, mul(rz(p.spineRoll * D), mul(rx(p.spinePitch * D), ry(p.twist * D))));
  const Rh = mul(Rc, mul(rz(p.headRoll * D), rx(p.headPitch * D)));

  const P0: V3 = [p.px, p.hipH, p.pz];
  const hipL = add(P0, app(Rp, [-HIP_W, 0, 0]));
  const hipR = add(P0, app(Rp, [HIP_W, 0, 0]));
  const ctrl = add(P0, app(Rmid, [0, TORSO * 0.55, 0]));
  const neck = add(add(P0, app(Rmid, [0, TORSO * 0.5, 0])), app(Rc, [0, TORSO * 0.5, 0]));
  const shL = add(neck, app(Rc, [-SH_W, -0.012, 0]));
  const shR = add(neck, app(Rc, [SH_W, -0.012, 0]));

  const elL = add(shL, app(Rc, dirv(-1, p.la1, p.lf1, UPPER)));
  const wrL = add(elL, app(Rc, dirv(-1, p.la2, p.lf2, FORE)));
  const elR = add(shR, app(Rc, dirv(1, p.ra1, p.rf1, UPPER)));
  const wrR = add(elR, app(Rc, dirv(1, p.ra2, p.rf2, FORE)));

  const head = add(neck, app(Rh, [p.headX, NECK + HEAD_R + p.headY, 0]));

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

  const proj = (v: V3): P2 => {
    const yc = v[1] * CO - v[2] * SI;
    const zc = v[1] * SI + v[2] * CO;
    const s = 1 / (1 - zc * 0.16);
    return { x: cam.cx + v[0] * s * cam.U, y: cam.fy - yc * s * cam.U, s };
  };
  const q = (v: V3) => proj(T(v));

  const headP = q(head);
  return {
    pelvis: q(P0), ctrl: q(ctrl), neck: q(neck),
    hipL: q(hipL), hipR: q(hipR),
    kneeL: q(legL.mid), kneeR: q(legR.mid), footL: q(legL.end), footR: q(legR.end),
    shL: q(shL), shR: q(shR), elL: q(elL), elR: q(elR), wrL: q(wrL), wrR: q(wrR),
    head: headP, headPx: HEAD_R * headP.s * cam.U,
    anchor: proj([p.x, 0, 0]), air: p.air,
  };
}

export function drawRig(ctx: CanvasRenderingContext2D, r: Rig, U: number, color = '#000000') {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = LINE_W * U;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  // legs + pelvis bar (one continuous line, ends are plain round tips: no feet)
  ctx.moveTo(r.footL.x, r.footL.y);
  ctx.lineTo(r.kneeL.x, r.kneeL.y);
  ctx.lineTo(r.hipL.x, r.hipL.y);
  ctx.lineTo(r.hipR.x, r.hipR.y);
  ctx.lineTo(r.kneeR.x, r.kneeR.y);
  ctx.lineTo(r.footR.x, r.footR.y);
  // spine
  ctx.moveTo(r.pelvis.x, r.pelvis.y);
  ctx.quadraticCurveTo(r.ctrl.x, r.ctrl.y, r.neck.x, r.neck.y);
  // shoulders + arms (no hands)
  ctx.moveTo(r.wrL.x, r.wrL.y);
  ctx.lineTo(r.elL.x, r.elL.y);
  ctx.lineTo(r.shL.x, r.shL.y);
  ctx.lineTo(r.shR.x, r.shR.y);
  ctx.lineTo(r.elR.x, r.elR.y);
  ctx.lineTo(r.wrR.x, r.wrR.y);
  // neck stops at the rim of the hollow head
  const vx = r.head.x - r.neck.x, vy = r.head.y - r.neck.y;
  const vl = Math.hypot(vx, vy) || 1;
  ctx.moveTo(r.neck.x, r.neck.y);
  ctx.lineTo(r.head.x - (vx / vl) * r.headPx, r.head.y - (vy / vl) * r.headPx);
  ctx.stroke();
  // hollow head: outline only
  ctx.beginPath();
  ctx.arc(r.head.x, r.head.y, r.headPx, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}
