import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

/* ------------------------------------------------------------------ */
/*  Folding platform cart – all units in metres                        */
/* ------------------------------------------------------------------ */

export const DIMS = {
  platform: 0.72, // square platform side (fits a 600 x 600 mm washing machine)
  rampLength: 0.45,
  handleHeight: 0.8,
};

const P = DIMS.platform;
const HALF = P / 2;

// vertical layout (deployed, wheels down)
const WHEEL_R = 0.05;
const WHEEL_HW = 0.013;
const SKIRT_BOTTOM = 0.09;
const DECK_BOTTOM = 0.14;
const DECK_TOP = 0.152;
const MAT_TOP = 0.158;
const LIP_TOP = 0.182;
const HINGE_Y = 0.19;
const HINGE_Z = 0.37;
const RAMP_T = 0.03; // ramp channel depth
const RAMP_L = DIMS.rampLength;
const PIN_L = 0.3; // ramp stud distance along the ramp
const PIN_Y = -0.015; // stud sits mid-depth in the ramp's side flange
// stud / stay hardware stack (x measured outward from the centre line)
const STRUT_X = 0.3685; // stay plate centre (plate is 4 mm: 0.3665 – 0.3705)
const NUT_LOOSE = 0.3745; // wing-nut washer face when backed off
const NUT_TIGHT = 0.3705; // washer face when clamped on the stay

const HANDLE_PIVOT_Y = 0.171;
const HANDLE_PIVOT_Z = -0.315;
const HANDLE_HW = 0.322;
const HANDLE_L = 0.64;
const HANDLE_R = 0.011;
const HANDLE_TILT = (12 * Math.PI) / 180;

const CASTER_X = 0.325;
const CASTER_Z = 0.29;
const CASTER_PIVOT_Y = 0.12;
const CASTER_DROP = 0.07;

const STRUT_S = new THREE.Vector2(0.14, 0.165); // z, y pivot on platform (set back for a longer lever arm)

const ss = (x: number, a: number, b: number) => {
  const t = THREE.MathUtils.clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};
const lerp = THREE.MathUtils.lerp;

/* ------------------------------- textures ------------------------------- */
function studTexture(repeatX: number, repeatY: number) {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  g.fillStyle = "#555";
  g.fillRect(0, 0, 128, 128);
  const stud = (x: number, y: number) => {
    const grd = g.createRadialGradient(x, y, 0, x, y, 20);
    grd.addColorStop(0, "#fff");
    grd.addColorStop(0.6, "#bbb");
    grd.addColorStop(1, "rgba(85,85,85,0)");
    g.fillStyle = grd;
    g.beginPath();
    g.arc(x, y, 20, 0, Math.PI * 2);
    g.fill();
  };
  for (const [x, y] of [
    [32, 32],
    [96, 32],
    [32, 96],
    [96, 96],
    [64, 64],
    [0, 64],
    [128, 64],
    [64, 0],
    [64, 128],
  ])
    stud(x, y);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeatX, repeatY);
  t.anisotropy = 8;
  return t;
}

/* ------------------------------- materials ------------------------------- */
const paint = new THREE.MeshPhysicalMaterial({
  color: 0x6c7cc8,
  metalness: 0.4,
  roughness: 0.4,
  clearcoat: 0.5,
  clearcoatRoughness: 0.28,
});
const paintDark = new THREE.MeshPhysicalMaterial({
  color: 0x56649f,
  metalness: 0.4,
  roughness: 0.5,
  clearcoat: 0.3,
});
const zinc = new THREE.MeshStandardMaterial({ color: 0xcfd4de, metalness: 1, roughness: 0.28 });
const steel = new THREE.MeshStandardMaterial({ color: 0x8f97a6, metalness: 1, roughness: 0.38 });
const rubber = new THREE.MeshStandardMaterial({ color: 0x0f0f12, roughness: 0.82 });
const gripMat = new THREE.MeshStandardMaterial({ color: 0x17171c, roughness: 0.65 });

/* ------------------------------- helpers ------------------------------- */
function rbox(w: number, h: number, d: number, r = 0.0015) {
  const rr = Math.min(r, Math.min(w, h, d) / 2 - 0.0001);
  return new RoundedBoxGeometry(w, h, d, 2, rr);
}
function put<T extends THREE.Object3D>(parent: THREE.Object3D, o: T, x = 0, y = 0, z = 0): T {
  o.position.set(x, y, z);
  o.traverse((c) => {
    if ((c as THREE.Mesh).isMesh) {
      c.castShadow = true;
      c.receiveShadow = true;
    }
  });
  parent.add(o);
  return o;
}
function box(
  parent: THREE.Object3D,
  mat: THREE.Material,
  w: number,
  h: number,
  d: number,
  x: number,
  y: number,
  z: number,
  r = 0.0015
) {
  return put(parent, new THREE.Mesh(rbox(w, h, d, r), mat), x, y, z);
}
/** cylinder with its axis along X / Y / Z */
function cyl(
  parent: THREE.Object3D,
  mat: THREE.Material,
  axis: "x" | "y" | "z",
  radius: number,
  length: number,
  x: number,
  y: number,
  z: number,
  seg = 24
) {
  const g = new THREE.CylinderGeometry(radius, radius, length, seg);
  if (axis === "x") g.rotateZ(Math.PI / 2);
  if (axis === "z") g.rotateX(Math.PI / 2);
  return put(parent, new THREE.Mesh(g, mat), x, y, z);
}
function rrShape(w: number, h: number, r: number) {
  const s = new THREE.Shape();
  const x = -w / 2;
  const y = -h / 2;
  s.moveTo(x + r, y);
  s.lineTo(x + w - r, y);
  s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r);
  s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h);
  s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r);
  s.quadraticCurveTo(x, y, x + r, y);
  return s;
}
/** Extrude a shape drawn in (z, y) and give it thickness along X */
function plateX(shape: THREE.Shape, thick: number) {
  const g = new THREE.ExtrudeGeometry(shape, { depth: thick, bevelEnabled: false, curveSegments: 20 });
  g.translate(0, 0, -thick / 2);
  g.rotateY(Math.PI / 2);
  return g;
}
function lathe(profile: [number, number][], axis: "x" | "y" = "x") {
  const g = new THREE.LatheGeometry(
    profile.map(([r, a]) => new THREE.Vector2(r, a)),
    48
  );
  if (axis === "x") g.rotateZ(Math.PI / 2);
  return g;
}

/* ------------------------------- the cart ------------------------------- */
export interface Cart {
  root: THREE.Group;
  update(p: number, m: number): void;
  setMachineVisible(v: boolean): void;
}

export function buildCart(): Cart {
  const root = new THREE.Group();
  const platform = new THREE.Group();
  root.add(platform);

  /* ---------- deck: plate, rubber mat, skirt and perimeter lip ---------- */
  box(platform, paint, P, DECK_TOP - DECK_BOTTOM, P, 0, (DECK_TOP + DECK_BOTTOM) / 2, 0, 0.003);
  const deckMat = new THREE.MeshStandardMaterial({
    color: 0x2c3053,
    roughness: 0.85,
    bumpMap: studTexture(24, 24),
    bumpScale: 1.6,
  });
  box(platform, deckMat, 0.692, MAT_TOP - DECK_TOP, 0.692, 0, (MAT_TOP + DECK_TOP) / 2, 0, 0.002);

  // perimeter lip (4 sides – the ramp-side one doubles as the hinge rail)
  const lipH = LIP_TOP - DECK_TOP;
  const lipY = DECK_TOP + lipH / 2;
  box(platform, paint, 0.012, lipH, P, HALF - 0.006, lipY, 0, 0.003);
  box(platform, paint, 0.012, lipH, P, -HALF + 0.006, lipY, 0, 0.003);
  box(platform, paint, P - 0.024, lipH, 0.012, 0, lipY, -HALF + 0.006, 0.003);
  box(platform, paint, P - 0.024, lipH, 0.012, 0, lipY, HALF - 0.006, 0.003);

  // downturned skirt with inward return flange
  const skH = DECK_BOTTOM - SKIRT_BOTTOM;
  const skY = SKIRT_BOTTOM + skH / 2;
  for (const s of [-1, 1]) {
    box(platform, paintDark, 0.003, skH, P, s * (HALF - 0.0015), skY, 0, 0.001);
    box(platform, paintDark, P - 0.006, skH, 0.003, 0, skY, s * (HALF - 0.0015), 0.001);
    box(platform, paintDark, 0.018, 0.003, P - 0.012, s * (HALF - 0.009), SKIRT_BOTTOM + 0.0015, 0, 0.001);
    box(platform, paintDark, P - 0.044, 0.003, 0.018, 0, SKIRT_BOTTOM + 0.0015, s * (HALF - 0.009), 0.001);
  }
  // corner rivets on the lip
  for (const sx of [-1, 1])
    for (const sz of [-1, 1])
      cyl(platform, zinc, "y", 0.0035, 0.002, sx * (HALF - 0.006), LIP_TOP + 0.0005, sz * (HALF - 0.02), 14);

  /* ---------------------- ramp hinge (platform half) ---------------------- */
  const hingeXs = [-0.27, 0, 0.27];
  for (const hx of hingeXs) {
    box(platform, zinc, 0.06, 0.052, 0.003, hx, 0.164, HALF + 0.0015, 0.001);
    box(platform, zinc, 0.03, 0.003, 0.012, hx - 0.0155, HINGE_Y - 0.0015, HINGE_Z - 0.003, 0.001);
    cyl(platform, zinc, "x", 0.0075, 0.029, hx - 0.0155, HINGE_Y, HINGE_Z);
  }

  /* ------------------------------ ramp ------------------------------ */
  // solve the down angle so the far edge just touches the floor
  let lo = 0.05;
  let hi = 1.0;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    const y = RAMP_L * Math.sin(mid) + RAMP_T * Math.cos(mid);
    if (y > HINGE_Y) hi = mid;
    else lo = mid;
  }
  const alpha = (lo + hi) / 2;

  const nuts: { g: THREE.Group; spin: THREE.Group; sx: number }[] = [];
  const ramp = new THREE.Group();
  ramp.position.set(0, HINGE_Y, HINGE_Z);
  platform.add(ramp);
  // local: +z along the ramp, -y thickness, x across
  box(ramp, paint, P, 0.004, RAMP_L, 0, -0.006, RAMP_L / 2, 0.001);
  const rampMat = new THREE.MeshStandardMaterial({
    color: 0x2c3053,
    roughness: 0.85,
    bumpMap: studTexture(24, 14),
    bumpScale: 1.6,
  });
  const matLen = RAMP_L - 0.075;
  box(ramp, rampMat, P - 0.04, 0.004, matLen, 0, -0.002, 0.045 + matLen / 2, 0.0015);
  const flangeH = 0.002 + RAMP_T;
  const flangeY = (0.002 - RAMP_T) / 2;
  for (const s of [-1, 1]) {
    box(ramp, paint, 0.004, flangeH, RAMP_L, s * (HALF - 0.002), flangeY, RAMP_L / 2, 0.0015);
    // rivets
    for (const rz of [0.05, RAMP_L - 0.05]) cyl(ramp, zinc, "x", 0.0045, 0.003, s * (HALF + 0.0015), flangeY, rz, 14);

    // --- stay stud: M8 stud pressed through a riveted backing plate, with a collar.
    // The stay slides on sideways between the collar and a wing nut, so nothing has to be threaded over a cap.
    box(ramp, zinc, 0.002, 0.026, 0.07, s * (HALF + 0.001), PIN_Y, PIN_L, 0.0008);
    for (const dz of [-0.026, 0.026]) cyl(ramp, zinc, "x", 0.0032, 0.0022, s * (HALF + 0.0021), PIN_Y, PIN_L + dz, 12);
    cyl(ramp, zinc, "x", 0.004, 0.04, s * 0.3755, PIN_Y, PIN_L, 16); // stud 0.3555 – 0.3955
    cyl(ramp, zinc, "x", 0.0065, 0.0045, s * 0.36425, PIN_Y, PIN_L, 20); // collar 0.362 – 0.3665
    // wing nut + washer (backs off for hooking the stay on, clamps it once seated)
    const nutG = new THREE.Group();
    nutG.position.set(s * NUT_LOOSE, PIN_Y, PIN_L);
    if (s < 0) nutG.rotation.y = Math.PI;
    ramp.add(nutG);
    cyl(nutG, zinc, "x", 0.009, 0.0016, 0.0008, 0, 0, 24); // washer
    const spin = new THREE.Group();
    nutG.add(spin);
    cyl(spin, zinc, "x", 0.0062, 0.007, 0.0051, 0, 0, 18);
    for (const o of [-1, 1]) box(spin, zinc, 0.006, 0.012, 0.0022, 0.0066, o * 0.0105, 0, 0.0008);
    nuts.push({ g: nutG, spin, sx: s });
  }
  box(ramp, paint, P, flangeH, 0.004, 0, flangeY, RAMP_L - 0.002, 0.0015);
  box(ramp, paint, P, flangeH, 0.004, 0, flangeY, 0.002, 0.0015);
  // under-ribs
  for (const f of [0.28, 0.52, 0.76]) box(ramp, paintDark, P - 0.012, 0.016, 0.012, 0, -0.016, RAMP_L * f, 0.002);
  // far end ledge
  box(ramp, paintDark, P - 0.008, 0.004, 0.02, 0, 0.0, RAMP_L - 0.016, 0.0012);
  // hinge – ramp half
  for (const hx of hingeXs) {
    box(ramp, zinc, 0.06, 0.003, 0.034, hx, 0.0035, 0.0175, 0.001);
    cyl(ramp, zinc, "x", 0.0075, 0.029, hx + 0.0155, 0, 0, 24);
    cyl(ramp, zinc, "x", 0.0035, 0.064, hx, 0, 0, 12);
  }

  /* ---------------------- ramp locking stays (2) ---------------------- */
  // Hook-type stays: pinned to a riveted bracket on the deck, with an open-sided notch at the free end that
  // drops over the ramp stud. The notch walls take the stay load in both tension and compression, the stay's
  // own weight keeps it seated (it leans forward past vertical), and the wing nut clamps it so it cannot jump
  // out on a bumpy ride. When stowed it rests in a small cradle on the deck edge instead of dangling.
  const pinWorld = new THREE.Vector2(HINGE_Z - PIN_Y, HINGE_Y + PIN_L); // z, y when ramp is vertical
  const strutLen = pinWorld.distanceTo(STRUT_S);
  const strutAngle = Math.atan2(pinWorld.y - STRUT_S.y, pinWorld.x - STRUT_S.x);
  // outline drawn in (u along the stay, v toward the side the stud arrives from), mapped to shape coords (-u, -v)
  const pts: [number, number][] = [];
  const arcPts = (cu: number, cv: number, r: number, a0: number, a1: number, n = 14) => {
    for (let i = 0; i <= n; i++) {
      const a = a0 + ((a1 - a0) * i) / n;
      pts.push([cu + r * Math.cos(a), cv + r * Math.sin(a)]);
    }
  };
  const d = strutLen;
  const bw = 0.009; // bar half width
  const hw = 0.013; // hook head half width
  const nw = 0.0043; // notch half width (stud is Ø8)
  arcPts(0, 0, bw, -Math.PI / 2, (-3 * Math.PI) / 2);
  pts.push([d - 0.034, bw], [d - 0.02, hw], [d - nw, hw], [d - nw, 0]);
  arcPts(d, 0, nw, Math.PI, 2 * Math.PI);
  pts.push([d + nw, hw], [d + 0.012, hw], [d + 0.016, hw - 0.004], [d + 0.016, -(hw - 0.004)], [d + 0.012, -hw]);
  pts.push([d - 0.02, -hw], [d - 0.034, -bw]);
  const strutShape = new THREE.Shape();
  pts.forEach(([u, v], i) => (i ? strutShape.lineTo(-u, -v) : strutShape.moveTo(-u, -v)));
  const pivotHole = new THREE.Path();
  pivotHole.absarc(0, 0, 0.0042, 0, Math.PI * 2, true);
  strutShape.holes.push(pivotHole);
  const strutGeo = plateX(strutShape, 0.004);
  const struts: THREE.Group[] = [];
  for (const s of [-1, 1]) {
    const g = new THREE.Group();
    g.position.set(s * STRUT_X, STRUT_S.y, STRUT_S.x);
    put(g, new THREE.Mesh(strutGeo, steel));
    platform.add(g);
    struts.push(g);

    // riveted pivot bracket on the outside of the deck edge (spreads the load over lip + deck + skirt)
    box(platform, zinc, 0.003, 0.044, 0.06, s * 0.3615, 0.159, STRUT_S.x, 0.001);
    for (const dz of [-0.022, 0.022])
      for (const dy of [-0.014, 0.014]) cyl(platform, zinc, "x", 0.0033, 0.002, s * 0.3635, 0.159 + dy, STRUT_S.x + dz, 12);
    cyl(platform, zinc, "x", 0.0065, 0.0035, s * 0.36475, STRUT_S.y, STRUT_S.x, 20); // spacer sleeve
    cyl(platform, zinc, "x", 0.004, 0.0125, s * 0.36625, STRUT_S.y, STRUT_S.x, 16); // shoulder bolt
    cyl(platform, zinc, "x", 0.0075, 0.0025, s * 0.37175, STRUT_S.y, STRUT_S.x, 20); // bolt head

    // stow cradle: shelf the hook head rests on + outer guard lip, on its own riveted plate
    const cz = STRUT_S.x - strutLen + 0.002;
    box(platform, zinc, 0.003, 0.036, 0.05, s * 0.3615, 0.164, cz, 0.001);
    for (const dz of [-0.017, 0.017]) cyl(platform, zinc, "x", 0.0033, 0.002, s * 0.3635, 0.164, cz + dz, 12);
    box(platform, zinc, 0.0155, 0.003, 0.04, s * 0.36775, 0.1505, cz, 0.0008); // shelf (top = 0.152)
    box(platform, zinc, 0.002, 0.029, 0.04, s * 0.3745, 0.1635, cz, 0.0008); // guard lip
  }

  /* ------------------------------ handle ------------------------------ */
  const handle = new THREE.Group();
  handle.position.set(0, HANDLE_PIVOT_Y, HANDLE_PIVOT_Z);
  platform.add(handle);
  const rc = 0.07;
  const V = (x: number, y: number) => new THREE.Vector3(x, y, 0);
  const path = new THREE.CurvePath<THREE.Vector3>();
  path.add(new THREE.LineCurve3(V(-HANDLE_HW, 0), V(-HANDLE_HW, HANDLE_L - rc)));
  path.add(
    new THREE.QuadraticBezierCurve3(V(-HANDLE_HW, HANDLE_L - rc), V(-HANDLE_HW, HANDLE_L), V(-HANDLE_HW + rc, HANDLE_L))
  );
  path.add(new THREE.LineCurve3(V(-HANDLE_HW + rc, HANDLE_L), V(HANDLE_HW - rc, HANDLE_L)));
  path.add(
    new THREE.QuadraticBezierCurve3(V(HANDLE_HW - rc, HANDLE_L), V(HANDLE_HW, HANDLE_L), V(HANDLE_HW, HANDLE_L - rc))
  );
  path.add(new THREE.LineCurve3(V(HANDLE_HW, HANDLE_L - rc), V(HANDLE_HW, 0)));
  put(handle, new THREE.Mesh(new THREE.TubeGeometry(path, 260, HANDLE_R, 24, false), paint));
  // rubber grip
  const gh = 0.19;
  const gr = 0.0165;
  put(
    handle,
    new THREE.Mesh(
      lathe([
        [0, -gh],
        [gr - 0.004, -gh],
        [gr, -gh + 0.004],
        [gr, gh - 0.004],
        [gr - 0.004, gh],
        [0, gh],
      ]),
      gripMat
    ),
    0,
    HANDLE_L,
    0
  );

  for (const s of [-1, 1]) {
    const lx = s * HANDLE_HW;
    // pivot knuckle on the handle leg
    cyl(handle, zinc, "x", 0.0125, 0.03, lx, 0, 0, 24);
    // fixed bracket (two plates + base) on the deck
    const sh = new THREE.Shape();
    const ph = HANDLE_PIVOT_Y - MAT_TOP;
    sh.moveTo(-0.026, 0);
    sh.lineTo(0.026, 0);
    sh.lineTo(0.0185, ph);
    sh.absarc(0, ph, 0.0185, 0, Math.PI, false);
    sh.lineTo(-0.026, 0);
    const bg = plateX(sh, 0.003);
    for (const o of [-1, 1]) put(platform, new THREE.Mesh(bg, paint), lx + o * 0.0185, MAT_TOP, HANDLE_PIVOT_Z);
    box(platform, paint, 0.046, 0.003, 0.056, lx, MAT_TOP + 0.0015, HANDLE_PIVOT_Z, 0.001);
    cyl(platform, zinc, "x", 0.004, 0.05, lx, HANDLE_PIVOT_Y, HANDLE_PIVOT_Z, 16);
    for (const o of [-1, 1])
      cyl(platform, zinc, "x", 0.0065, 0.003, lx + o * 0.0255, HANDLE_PIVOT_Y, HANDLE_PIVOT_Z, 18);
  }

  /* ------------------------------ wheels ------------------------------ */
  const casters: { g: THREE.Group; sx: number }[] = [];
  const forkPlate = plateX(rrShape(0.044, 0.106, 0.012), 0.003);
  forkPlate.translate(0, -0.033, 0);
  const tireGeo = lathe([
    [0.03, -0.0125],
    [0.044, -0.0128],
    [0.0488, -0.0105],
    [0.0499, -0.006],
    [0.05, 0],
    [0.0499, 0.006],
    [0.0488, 0.0105],
    [0.044, 0.0128],
    [0.03, 0.0125],
  ]);
  const hubGeo = lathe([
    [0, -0.0115],
    [0.027, -0.0115],
    [0.031, -0.008],
    [0.031, 0.008],
    [0.027, 0.0115],
    [0, 0.0115],
  ]);
  const earGeo = rbox(0.03, 0.037, 0.003, 0.001);
  for (const sx of [-1, 1])
    for (const sz of [-1, 1]) {
      const px = sx * CASTER_X;
      const pz = sz * CASTER_Z;
      // fixed ears + pin under deck
      for (const o of [-1, 1])
        put(platform, new THREE.Mesh(earGeo, paint), px + 0.001, CASTER_PIVOT_Y + 0.0015, pz + o * 0.0375);
      cyl(platform, zinc, "z", 0.0035, 0.08, px, CASTER_PIVOT_Y, pz, 12);
      box(platform, paint, 0.03, 0.003, 0.078, px + 0.001, DECK_BOTTOM - 0.0015, pz, 0.001);

      const g = new THREE.Group();
      g.position.set(px, CASTER_PIVOT_Y, pz);
      platform.add(g);
      cyl(g, zinc, "z", 0.0065, 0.05, 0, 0, 0, 20);
      box(g, paint, 0.012, 0.0165, 0.05, 0, 0.009, 0, 0.001);
      box(g, paint, 0.037, 0.003, 0.044, 0, 0.0185, 0, 0.001);
      for (const o of [-1, 1]) put(g, new THREE.Mesh(forkPlate, paint), o * 0.0165, 0, 0);
      put(g, new THREE.Mesh(tireGeo, rubber), 0, -CASTER_DROP, 0);
      put(g, new THREE.Mesh(hubGeo, zinc), 0, -CASTER_DROP, 0);
      cyl(g, zinc, "x", 0.005, 0.039, 0, -CASTER_DROP, 0, 14);
      for (const o of [-1, 1]) cyl(g, zinc, "x", 0.0075, 0.003, o * 0.0195, -CASTER_DROP, 0, 6);
      casters.push({ g, sx });
    }

  /* ------------------ washing machine (scale reference) ------------------ */
  const machine = new THREE.Group();
  {
    const white = new THREE.MeshPhysicalMaterial({ color: 0xf4f5f7, roughness: 0.3, metalness: 0.05, clearcoat: 0.6 });
    const glass = new THREE.MeshPhysicalMaterial({ color: 0x10151f, roughness: 0.05, metalness: 0, clearcoat: 1 });
    const body = new THREE.Group();
    put(body, new THREE.Mesh(new RoundedBoxGeometry(0.6, 0.85, 0.6, 4, 0.02), white), 0, 0.425, 0);
    // front is +z in local space
    put(body, new THREE.Mesh(new THREE.TorusGeometry(0.19, 0.022, 20, 64), zinc), 0, 0.4, 0.3);
    put(body, new THREE.Mesh(new THREE.CircleGeometry(0.175, 48), glass), 0, 0.4, 0.3 + 0.001);
    put(
      body,
      new THREE.Mesh(rbox(0.54, 0.09, 0.006, 0.003), new THREE.MeshStandardMaterial({ color: 0x2a2f3a, roughness: 0.3 })),
      0,
      0.775,
      0.301
    );
    cyl(body, zinc, "z", 0.028, 0.02, 0.17, 0.775, 0.31, 28);
    machine.add(body);
    body.rotation.y = Math.PI / 2;
    machine.position.set(0, MAT_TOP, 0.01);
    machine.visible = false;
    platform.add(machine);
  }

  /* ------------------------------ kinematics ------------------------------ */
  const update = (p: number, m: number) => {
    const w = ss(p, 0, 0.28);
    const r = ss(p, 0.28, 0.6);
    const h = ss(p, 0.6, 0.92);
    const s = ss(m, 0.8, 1) * ss(p, 0.92, 1);

    const deployedAngle = lerp(-alpha, Math.PI / 2, ss(m, 0, 0.8));
    ramp.rotation.x = -lerp(Math.PI, deployedAngle, r);
    handle.rotation.x = lerp(Math.PI / 2, -HANDLE_TILT, h);

    const beta = (1 - w) * (Math.PI / 2);
    for (const c of casters) c.g.rotation.z = -c.sx * beta;

    // stay swings up and hooks onto the stud (swing), then the wing nuts wind down onto it (tight)
    const swing = ss(s, 0, 0.88);
    const tight = ss(s, 0.9, 1);
    const A = lerp(Math.PI, strutAngle, swing);
    for (const st of struts) st.rotation.x = -A;
    for (const n of nuts) {
      n.g.position.x = n.sx * lerp(NUT_LOOSE, NUT_TIGHT, tight);
      n.spin.rotation.x = -n.sx * tight * Math.PI * 4;
    }

    // lower the whole platform as the wheels tuck in
    const wheelBottom = CASTER_PIVOT_Y - (CASTER_DROP + WHEEL_R) * Math.cos(beta) - WHEEL_HW * Math.sin(beta);
    const plateBottom = CASTER_PIVOT_Y - 0.086 * Math.cos(beta) - 0.0165 * Math.sin(beta);
    const lowest = Math.min(SKIRT_BOTTOM, wheelBottom, plateBottom);
    platform.position.y = -lowest;
  };
  update(1, 0);

  return {
    root,
    update,
    setMachineVisible(v: boolean) {
      machine.visible = v;
    },
  };
}
