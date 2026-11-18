"""Model: one loaded character (meshes, skeleton, baked clips, hold-physics chains)."""
from __future__ import annotations

import math
import traceback

import numpy as np
from PySide6 import QtGui

from .combat_data import _ATK_STRAT
from .config import BAKE_FPS, HOLD_DANGLE_WORDS, HOLD_JIGGLE_WORDS, HOLD_SKIP_WORDS
from .gltf import (
    Clip, GLTF, Prim, Skin, bleed_alpha, compute_normals, foreign_image, norm_name,
    sample_channel, sample_channels, sample_plan, smooth_normals, toks
)
from .mathutil import compose_trs, decompose, pelvis_track


class Model:
    def __init__(self, path):
        g = GLTF(path); self.g = g; j = g.j
        nodes = j.get("nodes", []); N = self.N = len(nodes)
        self.names = [n.get("name", f"node{i}") for i, n in enumerate(nodes)]
        self.parent = np.full(N, -1, dtype=np.int64)
        for i, n in enumerate(nodes):
            for c in n.get("children", []): self.parent[c] = i
        T = np.zeros((N, 3)); Q = np.tile([0, 0, 0, 1.0], (N, 1)); S = np.ones((N, 3))
        for i, n in enumerate(nodes):
            if "matrix" in n:
                t, q, s = decompose(np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T)
            else:
                t = n.get("translation", [0, 0, 0]); q = n.get("rotation", [0, 0, 0, 1]); s = n.get("scale", [1, 1, 1])
            T[i], Q[i], S[i] = t, np.asarray(q) / max(np.linalg.norm(q), 1e-12), s
        self.rest_T, self.rest_Q, self.rest_S = T, Q, S
        depth = np.zeros(N, dtype=np.int64)
        for i in range(N):
            d, p, guard = 0, self.parent[i], 0
            while p >= 0 and guard < N: d += 1; p = self.parent[p]; guard += 1
            depth[i] = d
        self.depth = depth
        nchild = np.zeros(N, dtype=np.int64)
        for i in range(N):
            if self.parent[i] >= 0: nchild[self.parent[i]] += 1
        self.is_leaf = nchild == 0                 # leaves are props / weapons, not rig structure
        self.levels = [np.where(depth == d)[0] for d in range(int(depth.max()) + 1)] if N else []
        self.level_parents = [self.parent[idx] for idx in self.levels]     # looked up once, not per level per frame

        # ---- skins ----
        self.skins, base = [], 0
        for s in j.get("skins", []):
            sk = Skin(); sk.joints = np.array(s["joints"], dtype=np.int64)
            if "inverseBindMatrices" in s:
                sk.ibm = g.accessor(s["inverseBindMatrices"]).astype(np.float64).reshape(-1, 4, 4).transpose(0, 2, 1)
            else:
                sk.ibm = np.tile(np.eye(4), (len(sk.joints), 1, 1))
            sk.base = base; base += len(sk.joints); self.skins.append(sk)
        self.total_joints = max(base, 1)

        # ---- meshes ----
        self.img_cache = {}
        # primitive src -> (glb path, image index) for textures this file does
        # not ship, filled in by a character profile (see foreign_image)
        self.tex_from = {}
        self.prims = []
        # Morph weights: Blue Archive exports every mouth shape as its own mesh and
        # picks one by animating a blend-shape weight (0 = shown, 1 = collapsed to a point).
        self.node_w0 = np.zeros(N)
        for ni, n in enumerate(nodes):
            ws = n.get("weights") or (j["meshes"][n["mesh"]].get("weights") if "mesh" in n else None)
            if ws: self.node_w0[ni] = ws[0]
        self.node_w = self.node_w0.copy()
        for ni, n in enumerate(nodes):
            if "mesh" not in n: continue
            mesh = j["meshes"][n["mesh"]]
            for pr in mesh["primitives"]:
                if pr.get("mode", 4) != 4: continue
                at = pr["attributes"]
                if "POSITION" not in at: continue
                P = Prim()
                pos = g.accessor(at["POSITION"]).astype(np.float32); V = len(pos)
                idx = g.accessor(pr["indices"]).astype(np.uint32).reshape(-1) if "indices" in pr else np.arange(V, dtype=np.uint32)
                idx = idx[: len(idx) // 3 * 3]
                nrm = g.accessor(at["NORMAL"]).astype(np.float32) if "NORMAL" in at else compute_normals(pos, idx)
                uv = g.accessor(at["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in at else np.zeros((V, 2), np.float32)
                skinned = n.get("skin") is not None and "JOINTS_0" in at and "WEIGHTS_0" in at
                if skinned:
                    jn = g.accessor(at["JOINTS_0"]).astype(np.float32)
                    w = g.accessor(at["WEIGHTS_0"]).astype(np.float32)
                    sm = w.sum(1); z = sm < 1e-6
                    w[z] = [1, 0, 0, 0]; sm[z] = 1.0; w /= sm[:, None]
                else:
                    jn = np.zeros((V, 4), np.float32); w = np.tile(np.array([1, 0, 0, 0], np.float32), (V, 1))
                P.pos, P.nrm, P.uv, P.joints, P.weights, P.idx = pos, nrm, uv, jn, w, idx
                P.onrm = smooth_normals(pos, nrm)
                P.node = ni; P.skin = n["skin"] if skinned else -1
                P.base = self.skins[P.skin].base if skinned else 0
                mi = pr.get("material")
                mat = j["materials"][mi] if mi is not None else {}
                pbr = mat.get("pbrMetallicRoughness", {})
                P.basecol = tuple(pbr.get("baseColorFactor", [1, 1, 1, 1]))
                P.amode = {"OPAQUE": 0, "MASK": 1, "BLEND": 2}.get(mat.get("alphaMode", "OPAQUE"), 0)
                P.cutoff = mat.get("alphaCutoff", 0.5)
                P.src = None; P.repeat = (True, True)
                ti = pbr.get("baseColorTexture")
                if ti is not None:
                    tex = j["textures"][ti["index"]]
                    src = tex.get("source")
                    if src is None:
                        for ext in tex.get("extensions", {}).values():
                            if "source" in ext: src = ext["source"]; break
                    P.src = src
                    if tex.get("sampler") is not None and "samplers" in j:
                        sp = j["samplers"][tex["sampler"]]
                        P.repeat = (sp.get("wrapS", 10497) != 33071, sp.get("wrapT", 10497) != 33071)
                P.name = " ".join([mat.get("name", ""), mesh.get("name", ""), n.get("name", "")]).lower()
                unlit = any(k in P.name for k in ("halo", "glow", "effect"))
                face = any(k in P.name for k in ("face", "eye", "mouth", "brow", "cheek"))
                P.shade = 0.0 if unlit else (0.7 if face else 1.0)
                P.outline = (not unlit) and P.amode != 2 and not any(k in P.name for k in ("eye", "brow", "mouth", "cheek", "lash"))
                P.see_through = any(k in P.name for k in ("eye", "brow", "lash"))
                P.hide_morph = False
                # Set by a profile with `ride` clips (Model.ride_prims): this
                # primitive is part of a vehicle that is only drawn while she is
                # riding, and the ride transition fades it in and out.
                P.vehicle = False
                if pr.get("targets") and "POSITION" in pr["targets"][0]:
                    dpos = g.accessor(pr["targets"][0]["POSITION"]).astype(np.float32)
                    ext = float(np.ptp(pos + dpos, axis=0).max())
                    P.hide_morph = ext < 1e-4 * max(float(np.ptp(pos, axis=0).max()), 1e-9)
                self.prims.append(P)

        # ---- rest pose, bones ----
        W0 = self.pose_world(T, Q, S); self.W0 = W0
        self.height = max(float(np.ptp(self.posed_positions(W0)[:, 1])), 1e-6) if self.prims else 1.0
        self.head = self.find_bone(("head",))
        self.neck = self.find_bone(("neck",))
        self.spine = self.find_bone(("spine1",)) or self.find_bone(("spine",))
        self.pelvis = self.find_bone(("pelvis", "hips", "hip"))
        if self.pelvis is None:
            jset = set(int(x) for sk in self.skins for x in sk.joints)
            for jn_ in sorted(jset, key=lambda k: depth[k]):
                if self.parent[jn_] not in jset: self.pelvis = jn_; break
        if self.pelvis is None: self.pelvis = 0
        # ancestors of the pelvis: the only bones whose scale moves the whole body
        self.pelvis_chain, _nd = [], int(self.pelvis)
        while _nd >= 0:
            self.pelvis_chain.append(_nd); _nd = int(self.parent[_nd])
        self.pelvis_chain.reverse()
        lb = [(b, w) for b, w in ((self.spine, .12), (self.neck, .28), (self.head, .60)) if b is not None]
        tot = sum(w for _, w in lb) or 1.0
        self.look_bones = [(b, w / tot) for b, w in lb] if self.head is not None else []
        # ---- hold physics: secondary-motion chains (hair / coat / skirt / tie) ----
        # Roots = nodes whose name has a jiggle word but whose parent doesn't share
        # the same word stem (so BL_01 is a root, BL_02 is not). Each chain is then
        # walked down through jiggle-named children. Anything else (props/weapons)
        # is ignored. Limbs are stored separately for a weaker dangle.
        self.jiggle_chains = []
        try:
            children_of = [list(n.get("children", [])) for n in nodes]
            def _kind(nm):
                l = nm.lower()
                if "hair" in l or "ahoge" in l: return "hair"
                if "necktai" in l or "necktie" in l or "tie" in l or "scarf" in l: return "tie"
                return "cloth"
            seen = set()
            for i, nm in enumerate(self.names):
                l = nm.lower()
                if not any(k in l for k in HOLD_JIGGLE_WORDS): continue
                if any(k in l for k in HOLD_SKIP_WORDS): continue
                if i in seen: continue
                pl = self.names[int(self.parent[i])].lower() if self.parent[i] >= 0 else ""
                # non-root segment (parent shares a jiggle word) -> picked up by walk-down
                if pl and any(k in pl for k in HOLD_JIGGLE_WORDS) and _kind(nm) == _kind(self.names[int(self.parent[i])]):
                    continue
                chain, cur, guard = [i], i, 0
                while guard < 8:
                    guard += 1
                    nxt = [c for c in children_of[cur]
                           if 0 <= c < N and c not in chain
                           and any(k in self.names[c].lower() for k in HOLD_JIGGLE_WORDS)
                           and not any(k in self.names[c].lower() for k in HOLD_SKIP_WORDS)]
                    if not nxt: break
                    # prefer the child of the same family (hair_BL_02 follows hair_BL_01)
                    nxt.sort(key=lambda c: 0 if _kind(self.names[c]) == _kind(self.names[cur]) else 1)
                    cur = int(nxt[0])
                    if cur in chain: break
                    chain.append(cur); seen.add(cur)
                if len(chain) >= 1:
                    kind = _kind(self.names[chain[0]])
                    self.jiggle_chains.append(dict(nodes=chain, kind=kind))
                    seen.update(chain)
            self.dangle_bones = []
            for i, nm in enumerate(self.names):
                tk = toks(nm)
                if tk and tk[-1] in HOLD_DANGLE_WORDS:
                    self.dangle_bones.append(i)
        except Exception as e:
            print("[warn] jiggle detect failed:", e)
            self.jiggle_chains, self.dangle_bones = [], []
        self.face_yaw = 0.0
        for i, nm in enumerate(self.names):
            if "toe" in nm.lower() and self.parent[i] >= 0:
                v = W0[i][:3, 3] - W0[self.parent[i]][:3, 3]
                if math.hypot(v[0], v[2]) > 1e-4 * self.height:
                    phi = math.atan2(v[0], v[2])
                    self.face_yaw = -round(phi / (math.pi / 2)) * (math.pi / 2)
                break

        for P in self.prims: self.soften_head(P)

        # ---- animations (baked to a fixed rate, each one the first time it is played) ----
        # Baking used to be two thirds of the time a model took to load and nearly all of the
        # memory it held (Iroha: 36 clips, 110 MB), for clips that are mostly never played. Here a
        # clip is registered with its name and its length only - both come straight out of the
        # file's index - and Clip bakes itself on first use (see gltf.Clip, _bake_clip below).
        self.clips = {}
        self._times = {}                 # time accessor index -> float64 key times (shared by channels)
        self._ref = None                 # the idle pivot every clip's origin is measured from
        self._floor = None               # floor line of the idle pose, for the kneel clips
        for ai, a in enumerate(j.get("animations", [])):
            name = a.get("name", f"anim{ai}")
            dur = 0.0
            for ch in a["channels"]:
                tg = ch["target"]
                if "node" not in tg or tg["path"] not in ("translation", "rotation", "scale", "weights"): continue
                times = self._key_times(a["samplers"][ch["sampler"]]["input"])
                if len(times): dur = max(dur, float(times[-1]))
            dur = max(dur, 1 / 30)
            self.clips[name] = Clip(name, dur, baker=lambda c, a=a: self._bake_clip(c, a))
        self.rest_clip = Clip("rest", 1.0, baker=lambda c: self._bake_clip(c, None))
        self.idle_ref = self.find_clip("Cafe_Idle") or self.rest_clip

        # ---- B2: only bleed textures used exclusively by MASK/BLEND prims ----
        used_a = {p.src for p in self.prims if p.amode != 0}
        used_o = {p.src for p in self.prims if p.amode == 0}
        self.bleed_srcs = used_a - used_o

    # ---------------------------------------------------------------
    #  Clips, baked on demand
    # ---------------------------------------------------------------
    def _key_times(self, idx):
        t = self._times.get(idx)
        if t is None:
            t = self._times[idx] = self.g.accessor(idx).astype(np.float64).reshape(-1)
        return t

    def _bake_clip(self, c, a):
        """Fill in one clip: sample it at BAKE_FPS, then pin, drift, scale signature and origin.

        `a` is the glTF animation, or None for the rest clip. Called by Clip the first time any
        of its data is read. The numbers are the ones the old load-everything loop produced.
        """
        try:
            self._sample_clip(c, a)
        except Exception:
            # A broken animation must not take the character down with it: she holds her rest
            # pose for that clip instead (what a missing clip already looked like).
            traceback.print_exc()
            print(f"[warn] animation '{c.name}' could not be baked - using the rest pose for it.")
            self._sample_clip(c, None)
        P = pelvis_track(self.pelvis_chain, c.T, c.Q, c.S)
        d = P[-1] - P[0]; d[1] = 0
        c.drift = d if np.linalg.norm(d) > 0.02 * self.height else None   # does this clip have root motion?
        # Pin the pelvis instead of de-trending it linearly: Iroha's Exs_Cutin sits
        # still for 3.8s and then travels 7 units, and a linear correction overshoots
        # by several body heights mid-clip. pin[t] exactly cancels the travelled XZ,
        # so the feet stay planted and the offset is constant -> nothing to pop.
        pin = -(P - P[0]); pin[:, 1] = 0.0
        c.pin = pin
        # Some rigs embed a second skeleton using compensating bone scales
        # (Iroha: Tank_Move at 0.001 and character_root at 1000). Those only cancel
        # inside a single clip, so two clips with different scale rigs cannot be
        # blended at all. Only the pelvis chain counts - weapon/prop bones scaled
        # 0.01 <-> 1.0 are ordinary show/hide keys, not a rig mismatch.
        c.ssig = np.log(np.maximum(c.S[:, self.pelvis_chain].mean(axis=0).mean(axis=1), 1e-9))
        # A1 (optional step): many clips are authored away from the idle pivot - some
        # sideways, Hoshino's Exs_Cutin starts 1.7 body-heights low - so they jump on
        # their first frame. Give every clip the same starting pivot. Y is included:
        # it lines the character up with the floor the app positions her on, while all
        # motion *within* the clip (bob, crouch, leap) is untouched because pin zeroes Y.
        c.origin = self._origin_ref() - pelvis_track(self.pelvis_chain, c.T[0:1], c.Q[0:1], c.S[0:1])[0]
        # Kneel clips (Kneel_Attack_Start / Ing / End / Delay) START kneeling, with the pelvis
        # well below its standing height. The line above lifts frame 0 up to the idle pelvis
        # height, which leaves a kneeling character hovering. Ground those clips by their
        # LOWEST point (knee / shin) instead, so they rest on the same floor line the idle
        # pose stands on. Only the constant Y offset changes; the motion inside is untouched.
        if a is not None:
            nm = norm_name(c.name)
            if "kneel" in nm and "attack" in nm:
                try:
                    floor_y = self._floor_y()
                    c.origin = c.origin.copy()
                    c.origin[1] += floor_y - self._lowest(c)
                except Exception:
                    traceback.print_exc()                    # never let grounding break a clip

    def _sample_clip(self, c, a):
        T, Q, S = self.rest_T, self.rest_Q, self.rest_S
        if a is None:
            n = 2
            c.T, c.Q, c.S = np.repeat(T[None], n, 0), np.repeat(Q[None], n, 0), np.repeat(S[None], n, 0)
            c.Wt = np.repeat(self.node_w0[None], n, 0)
            return
        g = self.g
        dur = c.duration
        F = max(2, int(math.ceil(dur * BAKE_FPS)) + 1)
        ts = np.linspace(0, dur, F)
        cT = np.repeat(T[None], F, 0); cQ = np.repeat(Q[None], F, 0); cS = np.repeat(S[None], F, 0)
        cW = np.repeat(self.node_w0[None], F, 0)
        dest = {"translation": cT, "rotation": cQ, "scale": cS}
        plans = {}            # time accessor -> (idx, u), shared by every channel keyed on it
        groups = {}           # (time accessor, STEP?, path, value shape) -> ([nodes], [values])
        for ch in a["channels"]:
            tg = ch["target"]
            if "node" not in tg or tg["path"] not in ("translation", "rotation", "scale", "weights"): continue
            sm = a["samplers"][ch["sampler"]]
            node, path, interp = tg["node"], tg["path"], sm.get("interpolation", "LINEAR")
            times = self._key_times(sm["input"])
            vals = g.accessor(sm["output"]).astype(np.float64)
            if path == "weights":
                nt = max(1, vals.size // (len(times) * (3 if interp == "CUBICSPLINE" else 1)))
                cW[:, node] = sample_channel(times, vals.reshape(-1, nt), interp, ts, path)[:, 0]
                continue
            if interp == "CUBICSPLINE" and len(vals) == 3 * len(times):
                vals = vals[1::3]
            if len(times) < 2 or len(vals) != len(times) or vals.ndim != 2:
                dest[path][:, node] = sample_channel(times, vals, interp, ts, path)    # the odd one out
                continue
            key = (sm["input"], interp == "STEP", path, vals.shape)
            grp = groups.get(key)
            if grp is None: grp = groups[key] = ([], [])
            grp[0].append(node); grp[1].append(vals)
        for (inp, step, path, _shape), (nodes, vals) in groups.items():
            plan = plans.get(inp)
            if plan is None: plan = plans[inp] = sample_plan(self._key_times(inp), ts)
            v = sample_channels(np.stack(vals), plan[0], plan[1], step, path == "rotation")
            dest[path][:, nodes] = v.transpose(1, 0, 2)
        c.T, c.Q, c.S, c.Wt = cT, cQ, cS, cW

    def _origin_ref(self):
        """World position of the pelvis on frame 0 of the idle clip: the pivot every clip starts on."""
        r = self._ref
        if r is None:
            c = self.idle_ref
            r = self._ref = pelvis_track(self.pelvis_chain, c.T[0:1], c.Q[0:1], c.S[0:1])[0].copy()
        return r

    def _lowest(self, clip):
        Wk = self.pose_world(clip.T[0], clip.Q[0], clip.S[0])
        Wk[:, :3, 3] += clip.origin + clip.pin[0]
        pts = self.posed_positions(Wk, skip_fx=True)
        if len(pts) == 0: pts = self.posed_positions(Wk)
        return float(np.percentile(pts[:, 1], 0.2))

    def _floor_y(self):
        if self._floor is None:
            self._floor = self._lowest(self.idle_ref)
        return self._floor

    def bake_all(self):
        """Bake every clip now (tools and --dump, which want the whole model at once)."""
        for c in list(self.clips.values()) + [self.rest_clip]:
            c.T
        return self

    # ---------------------------------------------------------------
    def soften_head(self, P, amount=0.85):
        """C3: push head-vertex normals toward a sphere for the soft anime face.

        Skin only. The eye / brow / mouth / cheek meshes have their shading painted
        into the texture, so re-lighting them toward a sphere washes the irises out to
        flat grey. Runs after P.onrm exists, so the outline hull keeps the real normals.
        """
        if self.head is None or P.skin < 0 or P.shade == 0.0: return
        if "hair" in P.name or any(k in P.name for k in ("eye", "brow", "mouth", "cheek", "lash")):
            return
        hj = np.where(self.skins[P.skin].joints == self.head)[0]
        if len(hj) == 0: return
        wh = (P.weights * (P.joints.astype(np.int64) == hj[0])).sum(1)
        m = wh > 0.6
        if m.sum() < 30: return
        d = P.pos - P.pos[m].mean(0)
        d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-6)
        n = P.nrm[m] * (1 - amount) + d[m] * amount
        P.nrm[m] = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-6)

    # ---------------------------------------------------------------
    def get_image(self, src):
        if src is None: return None
        if src in self.img_cache: return self.img_cache[src]
        res = None
        try:
            # A profile may swap in a texture from another file (see
            # foreign_image / iroha.py); those keys are strings, not indices.
            over = self.tex_from.get(src)
            raw = foreign_image(over["glb"], over["image"]) if over else self.g.image_bytes(src)
            qi = QtGui.QImage.fromData(raw)
            if qi.isNull(): raise ValueError("decode failed")
            qi = qi.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
            w, h, bpl = qi.width(), qi.height(), qi.bytesPerLine()
            buf = bytes(qi.constBits())
            if bpl != w * 4:
                buf = np.frombuffer(buf, np.uint8).reshape(h, bpl)[:, : w * 4].tobytes()
            if over and over.get("flip_v"):
                # The same asset exported on its own does not always carry its UVs
                # the same way up as the character export that embeds it: Iroha's
                # tank has v == 1 - v for 93% of its vertices against the standalone
                # tank, so its atlas has to be turned over or it lands upside down.
                buf = np.frombuffer(buf, np.uint8).reshape(h, w, 4)[::-1].tobytes()
            res = (w, h, buf)
            if src in self.bleed_srcs: res = (w, h, bleed_alpha(res[2], w, h))
        except Exception as e:
            print("[warn] image", src, "failed:", e)
        self.img_cache[src] = res
        return res

    def find_bone(self, words):
        for i, n in enumerate(self.names):
            tk = toks(n)
            if tk and tk[-1] in words: return i
        return None

    def find_clip(self, token):
        t = norm_name(token); names = list(self.clips)
        for n in names:
            if norm_name(n) == t: return self.clips[n]
        c = [n for n in names if norm_name(n).endswith(t)] or [n for n in names if t in norm_name(n)]
        if not c:
            # 'Strat' for 'Start' is a known typo in these exports (_ATK_RE folds it
            # when it finds attack sets by name). Fold it here too, so a profile
            # naming a clip by hand does not have to know the export's spelling.
            c = [n for n in names if _ATK_STRAT.sub("attackstart", norm_name(n)) == t]
        return self.clips[min(c, key=len)] if c else None

    def pose_world(self, T, Q, S, post=None):
        L = compose_trs(T, Q, S); W = np.empty_like(L)
        pm = {}
        if post:
            for node, R in post: pm.setdefault(int(self.depth[node]), []).append((node, R))
        lp = self.level_parents
        for d, idx in enumerate(self.levels):
            if d == 0: W[idx] = L[idx]
            else: W[idx] = np.matmul(W[lp[d]], L[idx])
            if not pm: continue
            for node, R in pm.get(d, ()):
                p = W[node][:3, 3].copy()
                D = np.eye(4); D[:3, :3] = R; D[:3, 3] = p - R @ p
                W[node] = D @ W[node]
        return W

    def posed_positions(self, W, skip_fx=False):
        out = []
        for p in self.prims:
            if skip_fx and p.shade == 0.0: continue      # halos / glows / effects must not set the scale
            ph = np.concatenate([p.pos.astype(np.float64), np.ones((len(p.pos), 1))], 1)
            if p.skin >= 0:
                # Only the x/y/z rows are kept, so only those are computed, and an influence
                # slot that is zero for the whole primitive (most meshes use 2-3 of the 4) is
                # skipped. Same numbers, about half the work - this runs several times per load.
                sk = self.skins[p.skin]; bm = np.matmul(W[sk.joints], sk.ibm)[:, :3, :]
                acc = np.zeros((len(ph), 3))
                for k in range(4):
                    wk = p.weights[:, k:k + 1]
                    if not wk.any(): continue
                    ji = np.clip(p.joints[:, k].astype(np.int64), 0, len(bm) - 1)
                    acc += wk * np.einsum("vij,vj->vi", bm[ji], ph)
            else:
                acc = (ph @ W[p.node].T)[:, :3]
            out.append(acc)
        return np.concatenate(out)

    def posed0(self, clip):
        """Frame 0 of `clip` with the offset the rig applies when it plays it.

        Rig.evaluate adds `clip.origin + clip.pin` before anything is drawn, so
        a camera fitted to the raw pose frames the wrong part of the model.
        That offset is exactly zero for the idle clip every fit used to be built
        from, so this changes nothing for a model that keeps its own window -
        but it matters as soon as the window is sized for a DIFFERENT pose:
        Iroha's riding clips put her pelvis 1.23 up, and without the offset the
        camera frames empty air above her tank and crops its tracks off.
        """
        W = self.pose_world(clip.T[0], clip.Q[0], clip.S[0])
        W[:, :3, 3] += clip.origin + clip.pin[0]
        return W

    def compute_fit(self, W):
        pts = self.posed_positions(W, skip_fx=True)
        if len(pts) == 0: pts = self.posed_positions(W)      # everything is an effect prim
        lo, hi = np.percentile(pts, 0.2, axis=0), np.percentile(pts, 99.8, axis=0)
        pel = W[self.pelvis][:3, 3]
        return dict(cx=float(pel[0]), cz=float(pel[2]), miny=float(lo[1]), maxy=float(hi[1]), H=float(hi[1] - lo[1]))

    def ride_prims(self, foot_clip, ride_clip):
        """The primitives that only exist while she is riding: Iroha's tank.

        A rig with a vehicle in it keys the vehicle's root bone to ~0 and
        counter-scales the body by the same factor, so the vehicle's geometry
        collapses onto that bone's own origin and disappears while her body keeps
        its size (Iroha: Tank_Move at 0.001, character_root at 1000). That pair is
        what is looked for: the bones whose scale changes between the two clips
        AND whose changes cancel out, so the body comes back exactly the same size
        while everything hanging off the outside of them does not. The outermost
        of them is the vehicle root.

        A primitive belongs to the vehicle when every one of its vertices sits on
        that origin in the on-foot pose - collapsed geometry and nothing else,
        which is also why no name has to be guessed. Returns [] for a model that
        merely rescales itself between clips, i.e. no vehicle to fade.
        """
        ls0 = np.log(np.maximum(foot_clip.S.mean(axis=0).mean(axis=1), 1e-9))
        ls1 = np.log(np.maximum(ride_clip.S.mean(axis=0).mean(axis=1), 1e-9))
        d = ls1 - ls0
        # pelvis_chain runs root -> pelvis, so the first bone to change is the outermost
        moved = [i for i in self.pelvis_chain if abs(d[i]) > 0.5]
        if len(moved) < 2 or abs(float(d[moved].sum())) > 0.5: return []
        root = moved[0]
        W = self.pose_world(foot_clip.T[0], foot_clip.Q[0], foot_clip.S[0])
        W[:, :3, 3] += foot_clip.origin + foot_clip.pin[0]
        pts, out, off = self.posed_positions(W), [], 0
        eps = 0.01 * self.height               # a 4-unit vehicle collapses to within 0.004
        for p in self.prims:
            v = pts[off:off + len(p.pos)]; off += len(p.pos)
            if len(v) and np.abs(v - W[root][:3, 3]).max() <= eps:
                out.append(p)
        return out

    def dump(self):
        print(f"nodes={self.N} skins={len(self.skins)} joints={self.total_joints} prims={len(self.prims)}")
        print("head/neck/spine/pelvis:", [self.names[i] if i is not None else None for i in (self.head, self.neck, self.spine, self.pelvis)])
        print("face_yaw(deg):", math.degrees(self.face_yaw))
        print("animations:")
        for n, c in self.clips.items(): print(f"  {n}  ({c.duration:.2f}s, root-motion={'yes' if c.drift is not None else 'no'})")
        print("primitives:")
        for p in self.prims:
            print(f"  {p.name.strip()!r} verts={len(p.pos)} skin={p.skin} alpha={p.amode} tex={p.src} shade={p.shade}")
        self.diagnose()

    def diagnose(self):
        """A1/A2/A4 report: which clips drift, start off-centre, or key scale to zero."""
        h = self.height
        idle = self.find_clip("Cafe_Idle") or self.rest_clip
        ref = self.pose_world(idle.T[0], idle.Q[0], idle.S[0])[self.pelvis][:3, 3]
        for n, c in self.clips.items():
            w0 = self.pose_world(c.T[0], c.Q[0], c.S[0])[self.pelvis][:3, 3]
            w1 = self.pose_world(c.T[-1], c.Q[-1], c.S[-1])[self.pelvis][:3, 3]
            drift = math.hypot(w1[0] - w0[0], w1[2] - w0[2]) / h * 100
            start = math.hypot(w0[0] - ref[0], w0[2] - ref[2]) / h * 100
            hidden = [self.names[i] for i in np.where(c.S.min(axis=(0, 2)) < 0.05)[0]]
            print(f"{n:22s} drift={drift:5.1f}%h root-motion={'yes' if c.drift is not None else 'no '} "
                  f"start-offset={start:5.1f}%h zero-scale={hidden[:6]}")
