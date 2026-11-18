"""Renderer: moderngl, MSAA, GPU skinning, Blue-Archive-style toon drawing."""
from __future__ import annotations

import math

import numpy as np
import moderngl

from .mathutil import mat_bytes, perspective, translate
from .shaders import FS_MAIN, FS_OUT, VS_MAIN, VS_OUT

IDENTITY_BYTES = mat_bytes(np.eye(4))


def U(prog, name, val):
    try: u = prog[name]
    except KeyError: return
    if isinstance(val, (bytes, bytearray)): u.write(val)
    else: u.value = val


class Renderer:
    FRAC, PADB, FOV = 0.58, 0.07, math.radians(16)

    def __init__(self, model, fit, ctx=None):
        self.m, self.fit = model, fit
        ctx = self.ctx = ctx if ctx is not None else moderngl.create_standalone_context(require=330)
        self.pg = ctx.program(vertex_shader=VS_MAIN, fragment_shader=FS_MAIN)
        self.po = ctx.program(vertex_shader=VS_OUT, fragment_shader=FS_OUT)
        self.bone_tex = ctx.texture((4, model.total_joints), 4, dtype="f4")
        self.bone_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.bone_tex.write(np.tile(np.eye(4, dtype="f4").T, (model.total_joints, 1, 1)).tobytes())
        white = ctx.texture((1, 1), 4, b"\xff\xff\xff\xff")
        tex_cache = {}
        for p in model.prims:
            img = model.get_image(p.src)
            key = (p.src, p.repeat)
            if img is None: p.tex = white
            elif key in tex_cache: p.tex = tex_cache[key]
            else:
                tx = ctx.texture((img[0], img[1]), 4, img[2])
                tx.build_mipmaps(); tx.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                try: tx.anisotropy = 8.0
                except Exception: pass
                tx.repeat_x, tx.repeat_y = p.repeat
                p.tex = tex_cache[key] = tx
            data = np.hstack([p.pos, p.nrm, p.uv, p.joints, p.weights, p.onrm]).astype("f4", copy=False)
            vbo = ctx.buffer(data); ibo = ctx.buffer(np.ascontiguousarray(p.idx, dtype="u4"))
            fmt = "3f 3f 2f 4f 4f 3f"
            names = ("in_pos", "in_nrm", "in_uv", "in_joints", "in_weights", "in_onrm")
            p.vao = ctx.vertex_array(self.pg, [(vbo, fmt, *names)], ibo, skip_errors=True)
            p.vao_o = ctx.vertex_array(self.po, [(vbo, fmt, *names)], ibo, skip_errors=True)
        self.prims = sorted(model.prims, key=lambda q: q.amode)
        self.fbo = self.fbo_rs = None
        self.w = self.h = 0
        # world units per rendered pixel. set_size() fills in the real number; this
        # default keeps anything that reads it (the ragdoll's limb pendulums) safe
        # before the first resize.
        self.world_per_px = 1.0
        # how much wider than the fit the current view is, see set_fit(scale)
        self.fit_scale = 1.0
        # camera distance in rendered px (the "f" of the 3D ragdoll's f/(f+z)),
        # filled in by _project(); 1.0 keeps anything that reads it safe first
        self.lens_px = 1.0
        # 0..1 opacity of the vehicle primitives, written by Pet every frame from
        # the ride transition. 1 = she is riding and it is solid (see Model.ride_prims).
        self.ride_fade = 1.0
        self.PV = np.eye(4)
        self.set_fit(fit)

    def set_fit(self, fit, scale=1.0):
        """Frame the camera around a pose. Called again when she mounts a vehicle,
        whose window is a different size and looks at a different fit (Pet.set_ride).

        `scale` is the window a limp body gets (Pet.rag_window): a tumbling body
        reaches well outside the standing pose's frame and the window edge clipped
        it, so the window is multiplied by `scale` and the camera is opened up to
        match.

        What it must NOT do is move her. The camera (eye, distance) is therefore
        computed from the fit ALONE and is the same number for every scale; all the
        scale does is shorten the focal length and shift the lens down (see
        _project), which together make the projection exactly homothetic about the
        eye. So her image is the old one, pixel for pixel, with more world around
        it - no change of size, no change of shape, no change of perspective, and
        `world_per_px` (world units per rendered pixel) still the world's own
        number, so the limb pendulums, the outline width and the 3D depth mapping
        all still measure her rather than the frame.

        Consequence for the rest of the code: she is 58% of her window tall exactly
        as before, so char_h() / rag_h() must read the STANDING window
        (PetWindow.stand_h), not H.
        """
        self.fit = fit
        self.fit_scale = k = max(0.05, float(scale))
        # the world's own numbers from here down: the camera frames V_world of it,
        # and _project() opens that up by k (the window shows k * this)
        self.V_world = V = fit["H"] / self.FRAC
        self.dist = V / (2 * math.tan(self.FOV / 2))
        cy = fit["miny"] + V / 2 - self.PADB * V
        self.eye = np.array([fit["cx"], cy, fit["cz"] + self.dist])
        self.view = translate(-self.eye)
        if self.w: self._project()

    def _project(self):
        k = self.fit_scale
        d, H = self.dist, self.fit["H"] * k        # the extra world shown also widens the depth range
        self.proj = perspective(self.FOV, self.w / self.h, max(d - H * 2.0, d * 0.05), d + H * 3.0,
                                zoom=1.0 / k,
                                # lens shift: without it the shorter focal would pull her up into
                                # the new frame. Her feet sit PADB from the bottom of the window,
                                # which is NDC -(1 - 2*PADB) whatever the window's size, and the
                                # shorter focal alone puts them at -(1 - 2*PADB) / k.
                                shift_y=(2.0 * self.PADB - 1.0) * (1.0 - 1.0 / k))
        self.world_per_px = self.V_world * k / self.h
        self.lens_px = d / self.h                  # camera distance in px (see pet_rag3d's depth scale)

    def set_size(self, w, h):
        if (w, h) == (self.w, self.h): return
        ctx = self.ctx
        if ctx.gc_mode is None:
            for o in (self.fbo, self.fbo_rs):
                if o is not None: o.release()
        self.w, self.h = w, h
        try:
            col = ctx.renderbuffer((w, h), 4, samples=4); dep = ctx.depth_renderbuffer((w, h), samples=4)
            self.fbo = ctx.framebuffer(color_attachments=[col], depth_attachment=dep)
        except Exception:
            col = ctx.renderbuffer((w, h), 4); dep = ctx.depth_renderbuffer((w, h))
            self.fbo = ctx.framebuffer(color_attachments=[col], depth_attachment=dep)
        self.fbo_rs = ctx.framebuffer(color_attachments=[ctx.renderbuffer((w, h), 4)])
        self._project()

    def project(self, p3, M):
        v = self.PV @ (M @ np.array([p3[0], p3[1], p3[2], 1.0]))
        x, y = v[0] / v[3], v[1] / v[3]
        return (x * 0.5 + 0.5) * self.w, (1 - (y * 0.5 + 0.5)) * self.h

    def draw(self, world, M, light, cfg):
        ctx, m = self.ctx, self.m
        if m.skins:
            rows = [np.matmul(world[sk.joints], sk.ibm).transpose(0, 2, 1) for sk in m.skins]
            self.bone_tex.write(np.ascontiguousarray(np.concatenate(rows).astype("f4")).tobytes())
        self.fbo.use(); self.fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        ctx.enable(moderngl.DEPTH_TEST | moderngl.BLEND)
        ctx.disable(moderngl.CULL_FACE)
        ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
        self.PV = self.proj @ self.view
        self.bone_tex.use(1)
        projection, view, transform = mat_bytes(self.proj), mat_bytes(self.view), mat_bytes(M)
        for pg in (self.pg, self.po):
            U(pg, "u_proj", projection); U(pg, "u_view", view); U(pg, "u_model", transform)
            U(pg, "u_bones", 1)
        pg = self.pg
        lc = np.array([1.03, 1.01, 0.98]) * cfg["exposure"]
        U(pg, "u_light", tuple(float(x) for x in light)); U(pg, "u_campos", tuple(float(x) for x in self.eye))
        U(pg, "u_light_col", tuple(float(x) for x in lc))
        U(pg, "u_shadow_tint", (0.74, 0.78, 0.96)); U(pg, "u_rim_col", (0.72, 0.88, 1.0))
        U(pg, "u_thresh", float(cfg["toon_threshold"])); U(pg, "u_soft", max(float(cfg["toon_softness"]), 1e-4))
        U(pg, "u_shadow", float(cfg["shadow_strength"])); U(pg, "u_rim", float(cfg["rim"]))
        U(pg, "u_spec", float(cfg["spec"])); U(pg, "u_sat", float(cfg["saturation"]))
        U(pg, "u_deep", float(cfg["deep_shadow"]))
        U(pg, "u_tex", 0)
        U(self.po, "u_tex", 0)
        U(self.po, "u_width", float(cfg["outline_px"]) * self.world_per_px)
        U(self.po, "u_dark", float(cfg["outline_dark"]))
        nw = m.node_w
        shown = lambda p: not (p.hide_morph and nw[p.node] >= 0.5)
        # A vehicle (Iroha's tank) only exists in the riding pose, and the pose is
        # hard-cut rather than cross-faded - the two clips disagree about bone
        # scale, so blending them is what Rig.play refuses to do. What is faded
        # instead is the vehicle: mid-transition it goes in the blended pass at
        # `ride_fade` opacity, so it materialises around her instead of blinking
        # in between two frames. Only her body is opaque then, and it composites
        # over it, which is what a thing arriving out of nothing should look like.
        rf = self.ride_fade
        opaque, blends, ghosts = [], [], []
        for p in self.prims:
            if not shown(p): continue
            f = rf if p.vehicle else 1.0
            if f <= 0.002: continue
            if f < 0.998: ghosts.append((p, f))
            elif p.amode == 2: blends.append(p)
            else: opaque.append(p)
        for p in opaque: self._draw(p, world, False)
        if cfg["outline_px"] > 0.01:
            for p in opaque:
                if p.outline: self._draw(p, world, True)
        # C4: eyes drawn again with an inverted depth test, so they read through the bangs.
        # Off by default: with no stencil to separate the layers this shows the eye
        # mesh's *hidden* backing geometry through the face rather than the visible
        # eye, which greys the eyes out. Only worth trying on a model whose eyes are
        # a single flat layer.
        eyes = [p for p in opaque if getattr(p, "see_through", False)] if cfg.get("eyes_through_bangs") else []
        if eyes:
            ctx.depth_func = '>'; self.fbo.depth_mask = False
            for p in eyes: self._draw(p, world, False, fade=0.4)
            ctx.depth_func = '<='; self.fbo.depth_mask = True
        try: self.fbo.depth_mask = False
        except Exception: pass
        for p, f in ghosts: self._draw(p, world, False, fade=f)
        for p in blends: self._draw(p, world, False)
        try: self.fbo.depth_mask = True
        except Exception: pass
        ctx.copy_framebuffer(self.fbo_rs, self.fbo)
        return self.fbo_rs.read(components=4, alignment=1)

    def _draw(self, p, world, outline, fade=1.0):
        prog = self.po if outline else self.pg
        p.tex.use(0)
        U(prog, "u_skinned", 1 if p.skin >= 0 else 0); U(prog, "u_base", int(p.base))
        U(prog, "u_node", mat_bytes(world[p.node]) if p.skin < 0 else IDENTITY_BYTES)
        U(prog, "u_basecol", tuple(float(x) for x in p.basecol))
        U(prog, "u_alpha_mode", int(p.amode)); U(prog, "u_cutoff", float(p.cutoff))
        if not outline:
            U(prog, "u_shade", float(p.shade)); U(prog, "u_fade", float(fade))
        (p.vao_o if outline else p.vao).render()
