"""Pet mixin: procedural motion, rendering each frame, click-through mask, painting."""
from __future__ import annotations

import math
import sys
import time

import numpy as np
from PySide6 import QtCore, QtGui
from PySide6.QtCore import QPointF, QRectF, QSize, Qt

from . import vfx
from .config import TWRAP
from .mathutil import clamp, fade_back, m4, rot_x, rot_y, rot_z, rot_z4, scale4, translate, wrap_pi


# =============================================================================
#  The pet window
# =============================================================================
def heart_path(s):
    p = QtGui.QPainterPath(); p.moveTo(0, s * 0.8)
    p.cubicTo(-s * 1.1, s * 0.1, -s * 0.7, -s * 0.9, 0, -s * 0.35)
    p.cubicTo(s * 0.7, -s * 0.9, s * 1.1, s * 0.1, 0, s * 0.8)
    return p

def star_path(r):
    poly = QtGui.QPolygonF()
    for i in range(8):
        rr = r if i % 2 == 0 else r * 0.22; a = i * math.pi / 4
        poly.append(QPointF(math.cos(a) * rr, math.sin(a) * rr))
    p = QtGui.QPainterPath(); p.addPolygon(poly); p.closeSubpath(); return p


class PetRender:
    def procedural(self, dt, cur):
        cfg = self.cfg
        self.rot_return(dt); self.scan_step(dt)
        hx, hy = self.px + self.head_px[0], self.py + self.head_px[1]
        # look target: another character if one was picked, else the cursor while she is
        # attending to it, else a point off in the room (she is looking at nobody)
        tx_, ty_ = cur.x(), cur.y(); dist = None; idle_target = False
        peer = self.gaze_peer
        aim = self.atk_aim_point()                  # shooting: look down the barrel
        if aim is not None:
            tx_, ty_ = aim; dist = math.hypot(tx_ - hx, ty_ - hy)
        elif peer is not None and peer.isVisible() and self.gaze_left > 0:
            tx_ = peer.px + peer.head_px[0]; ty_ = peer.py + peer.head_px[1]
            dist = math.hypot(tx_ - hx, ty_ - hy)
        elif not self.cur_look:
            reach = self.W * 2.2
            tx_ = hx + self.idle_pt[0] * reach; ty_ = hy - self.idle_pt[1] * reach
            dist = math.hypot(tx_ - hx, ty_ - hy)
            idle_target = True
        depth = max(self.H * 1.4, dist) if dist is not None else self.H * 1.4
        ax = math.atan2(tx_ - hx, depth); ay = math.atan2(-(ty_ - hy), depth)
        running = self.is_running()
        moving = self.state == "walk" or running
        active = self.state in ("idle", "alert", "shot", "walk", "shoot", "flee") \
            and (peer is not None or self.cur_look or idle_target or aim is not None)
        k = 0.25 if moving else (0.3 if self.shot_key == "cutin" and self.state == "shot" else 1.0)
        if idle_target: k *= 0.6                    # an absent glance, not a stare
        tx, ty = (ax * k, ay * k) if active else (0.0, 0.0)
        f = 1 - math.exp(-dt * 7)
        self.gx += (tx - self.gx) * f; self.gy += (ty - self.gy) * f
        yo_user = self.yaw_base()                  # panel + hand rotation (steers a walk)
        yo = yo_user + self.scan_yaw               # plus the look-around swivel
        jumping = not self.grounded and self.air_kind == "jump" and not self.dragging
        # Hand rotation (wheel / rotate-drag) is the way she FACES. If it changes
        # while she walks, her path turns with it (she goes where she looks).
        dyo = wrap_pi(yo_user - self._yo_seen)
        if dyo and self.state == "walk": self.set_heading(self.heading + dyo)
        self._yo_seen = yo_user
        # a window top is a line: she settles to the middle depth there
        if self.support is not None and not self.ragdoll:
            self.depth *= math.exp(-dt * 2.0)
        psi = yo + self.body_yaw                              # where she faces now (world yaw)
        def toward(k, lim):                                   # a partial turn toward the gaze
            d = wrap_pi(self.gx - yo)                         # target azimuth vs her rest facing
            return yo + clamp(d * k, -lim, lim) * fade_back(d)
        if moving:
            tgt = self.heading                                # absolute: she faces where she walks
        elif jumping:
            d = 1 if abs(self.vx) < 20 else (1 if self.vx > 0 else -1)
            tgt = d * 0.9                                     # face the way she flies
        elif aim is not None:
            tgt = clamp(self.gx * 1.0, -0.9, 0.9)             # absolute: she squares up to who she shoots
        elif self.gaze_peer is not None:
            tgt = toward(0.8, 0.75)
        else:
            tgt = toward(0.5, 0.55)
        self.body_yaw += wrap_pi(tgt - psi) * (1 - math.exp(-dt * 6))
        self.body_yaw = wrap_pi(self.body_yaw)                # same pose, keeps the number small
        psi = yo + self.body_yaw
        # head: look at the gaze azimuth, measured from where she ACTUALLY faces
        if moving:
            dw = wrap_pi(psi)
            self.head_yaw = clamp(-dw * 0.35 * fade_back(dw, 1.8, 2.6) + self.gx, -1.0, 1.0)
        else:
            dh = wrap_pi(self.gx - psi)
            self.head_yaw = clamp(dh, -1.0, 1.0) * fade_back(dh)
        self.head_pitch = clamp(self.gy, -0.5, 0.55) - self.bow * 0.35
        # pendulum roll while carried / tumbling + hold-physics drive
        raw_vx, raw_vy = 0.0, 0.0
        if self.dragging:
            # While a pymunk pet is held, the physics owns her position - she is
            # moved by the grab joint, not by the window - so feed the springs her
            # real velocity off the pelvis rather than the window's, or the hair
            # would hang dead still while she swings.
            if self.rag_skel is not None:
                p = self.rag_skel.pose()
                raw_vx, raw_vy = p["vx"], p["vy"]
            else:
                raw_vx = (self.px - self.last_px) / max(dt, 1e-3)
                raw_vy = (self.py - self.last_py) / max(dt, 1e-3)
        elif not self.grounded and self.air_kind in ("thrown", "jump"):
            raw_vx, raw_vy = self.vx * 0.6, self.vy * 0.6
        self.last_px, self.last_py = self.px, self.py
        f = 1 - math.exp(-dt * 10)
        self.hold_vx += (raw_vx - self.hold_vx) * f
        self.hold_vy += (raw_vy - self.hold_vy) * f
        # wind gust: radial push from the cursor. Hair chains only (see
        # Rig.update_hold) - the body roll/pitch below never sees it.
        wvx, wvy, we = self.A.gust_wind_at(self.px + self.head_px[0], self.py + self.head_px[1])
        wf = 1 - math.exp(-dt * 14)
        self.wind_vx += (wvx - self.wind_vx) * wf
        self.wind_vy += (wvy - self.wind_vy) * wf
        # energy: 1 while held, decays over ~0.9s after release so hair settles
        tgt_e = 1.0 if self.dragging else (0.7 if (not self.grounded and self.air_kind in ("thrown", "jump")) else 0.0)
        self.hold_e += (tgt_e - self.hold_e) * (1 - math.exp(-dt * (12 if tgt_e > self.hold_e else 2.2)))
        yaw_m = self.model.face_yaw + self.yaw_base() + self.scan_yaw + self.body_yaw
        self.rig.update_hold(dt, self.hold_vx, self.hold_vy, self.hold_e, yaw_m,
                             wx=self.wind_vx, wy=self.wind_vy, we=we)
        # Roll is the "carried / thrown" lean. While she is limp the ragdoll angle
        # already owns the body rotation, so keep roll at zero or it would fight it
        # and pop when she gets back up.
        vxd = 0.0
        if self.dragging: vxd = raw_vx
        elif not self.ragdoll and not self.grounded and self.air_kind == "thrown": vxd = self.vx * 0.6
        rt = clamp(-vxd * 0.0006, -0.5, 0.5)
        self.roll_v += (60 * (rt - self.roll) - 9 * self.roll_v) * dt; self.roll += self.roll_v * dt
        # While she is limp the body angle owns her rotation (render_frame does
        # rot_z4(roll - rg_a)), so roll must be zero or the two would fight and
        # pop when she gets up. start_ragdoll already zeroes it; this catches the
        # case where she was carried into the ragdoll.
        if self.ragdoll and self.rag_skel is not None:
            self.roll = self.roll_v = 0.0
        # fore/aft swing while carried (pitch about the grab point) + vertical stretch
        # With pymunk she is not "carried" - physics swings her - so both of these
        # stay off, or they would fight the body and pop when she lands.
        held = self.dragging and self.rag_skel is None
        pt = clamp(self.hold_vy * 0.00035, -0.35, 0.35) if held else 0.0
        self.pitch_hold_v += (70 * (pt - self.pitch_hold) - 10 * self.pitch_hold_v) * dt
        self.pitch_hold += self.pitch_hold_v * dt
        if held:
            # fast lift stretches her a touch, fast sideways squashes; springs back on release
            self.sq_v += clamp(self.hold_vy * 0.00006, -0.12, 0.12) / max(dt, 1e-3) * dt * 0.15
        self.sq_v += (-180 * self.sq - 14 * self.sq_v) * dt; self.sq += self.sq_v * dt
        zt = 1.08 if (self.state == "shot" and self.shot_key == "cutin") else 1.0
        self.zoom += (zt - self.zoom) * (1 - math.exp(-dt * 8))

    def render_frame(self, cur):
        A, cfg, R = self.A, self.cfg, self.ch.renderer
        # smoothstep, not the raw clock: half-way through the fade the vehicle
        # should be half-visible, which a linear ramp front-loads into the first
        # tenth of it.
        R.ride_fade = 1.0 if self.ch.ride_clips is None else self.ride_t ** 2 * (3 - 2 * self.ride_t)
        # limb pendulums first: they run off last frame's world pose + last_M,
        # so they are always one frame behind the body - stable and cheap.
        self.rig.rag_post = self.rag_limb_post(self.dt) if self.ragdoll else []
        world = self.rig.evaluate(self.head_yaw, self.head_pitch,
                                  self.yaw_base() + self.scan_yaw + self.body_yaw)
        yaw = self.model.face_yaw + self.yaw_base() + self.scan_yaw + self.body_yaw
        if self.spin_t >= 0:
            p = clamp(self.spin_t / 1.1, 0, 1); yaw += math.tau * (p * p * (3 - 2 * p))
        f = R.fit
        feet = np.array([f["cx"], f["miny"], f["cz"]]); top = np.array([f["cx"], f["maxy"], f["cz"]])
        z = self.zoom * self.depth_scale()
        B = translate(feet) @ scale4((1 + 0.5 * self.sq) * z, (1 - self.sq) * z, (1 + 0.5 * self.sq) * z) @ m4(rot_y(yaw)) @ translate(-feet)
        # grab pivot: she hangs from the cursor, so swing rotates about the top
        M = (translate(top) @ m4(rot_x(self.pitch_hold)) @ rot_z4(self.roll) @ translate(-top)) @ B
        if self.ragdoll and self.rag3 is not None:
            # Fully 3D ragdoll: the matrix she went limp with, turned by her pelvis's
            # full 3D rotation about the pelvis (pet_rag3d.py). Yaw, squash and lean
            # are frozen into it, so nothing but the physics turns her while limp.
            M = self.rag3_model_matrix(world)
        elif self.ragdoll and (not self.dragging or self.rag_skel is not None):
            # Lay her over on the body angle, about her pelvis. rot_z4 is a
            # right-handed rotation in the feet-anchored frame, where +x is screen
            # right and +y is screen up, so -rg_a puts a positive rg_a (head
            # towards screen right) on screen. Both engines feed this from their
            # own angle: the rod sets rg_a directly, pymunk sets it to
            # +phi_pelvis in ragdoll_step_pm.
            #
            # The one case excluded is the legacy rod WHILE being dragged, where
            # the cursor owns her position instead. A pymunk pet keeps this
            # rotation even while held: physics is still driving her pose.
            p = (B @ np.append(world[self.model.pelvis][:3, 3], 1.0))[:3]
            M = translate(p) @ rot_z4(self.roll - self.rg_a) @ translate(-p) @ B
        # Free 3D orientation (pitch / tilt offsets). Applied LAST, in the screen
        # frame about the middle of her body, so yaw still spins about her own
        # vertical axis first. last_M deliberately stays WITHOUT it: the pymunk
        # ragdoll measures its skeleton and its Z axis from last_M, and has to keep
        # seeing the plain upright-in-the-plane pose. While she is limp the offset
        # eases back to zero (she falls from upright), then eases back in after.
        k_o = 1.0 - math.exp(-self.dt * 10.0)
        tp = 0.0 if self.ragdoll else math.radians(clamp(float(cfg.get("pitch_offset", 0.0)) + self.pitch_user, -90.0, 90.0))
        tt = 0.0 if self.ragdoll else math.radians(clamp(float(cfg.get("tilt_offset", 0.0)) + self.tilt_user, -40.0, 40.0))
        self.ori_p += (tp - self.ori_p) * k_o; self.ori_t += (tt - self.ori_t) * k_o
        self.last_M = M
        if abs(self.ori_p) > 1e-4 or abs(self.ori_t) > 1e-4:
            mid = np.array([f["cx"], (f["miny"] + f["maxy"]) * 0.5, f["cz"]])
            M = translate(mid) @ m4(rot_z(self.ori_t) @ rot_x(self.ori_p)) @ translate(-mid) @ M
        # light: cursor acts as a point light hovering in front of the screen
        if cfg["light_follow"]:
            cx, cy = self.px + self.W / 2, self.py + self.H * 0.4
            sw, sh = A.screen_size()
            tgt = np.array([clamp((cur.x() - cx) / (sw * 0.5) * 1.6, -2, 2), clamp(-(cur.y() - cy) / (sh * 0.5) * 1.6, -2, 2), 0.9])
        else:
            az, el = math.radians(cfg["light_az"]), math.radians(cfg["light_el"])
            tgt = np.array([math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az)])
        self.light += (tgt - self.light) * min(1.0, 1.0 - math.exp(-self.dt * 12))     # B7: frame-rate independent
        raw = R.draw(world, M, self.light, cfg)
        w, h = R.w, R.h
        self.raw = raw
        self.img = QtGui.QImage(raw, w, h, w * 4, QtGui.QImage.Format.Format_RGBA8888_Premultiplied).mirrored(False, True)
        self.img.setDevicePixelRatio(self.dpr)              # B8: no resample on fractional-DPI screens
        self.alpha = np.frombuffer(raw, np.uint8).reshape(h, w, 4)[::-1, :, 3]
        if self.model.head is not None:
            hxp, hyp = R.project(world[self.model.head][:3, 3], M)
            # stand_h(), not H: the nudge that lifts her head marker off the
            # skull is a fraction of HER height, and the limp window is bigger.
            self.head_px = (hxp / self.dpr * (self.W * self.dpr / w),
                            hyp / self.dpr * (self.H * self.dpr / h) - 0.05 * self.stand_h())
        if cfg["click_through"] and not A.args.no_mask:
            if sys.platform == "win32":
                if self.mask_key is not None: self.clearMask(); self.mask_key = None
            elif self.frame_n % 3 == 0 or self.mask_dirty: self.update_mask()
        elif self.mask_key is not None:
            self.clearMask(); self.mask_key = None
        self.update()

    # ---------------- click-through mask ----------------
    def bubble_geom(self):
        if not self.bubble: return None
        fm = QtGui.QFontMetrics(self.bfont)
        r = fm.boundingRect(0, 0, int(self.W * 0.82), 2000, TWRAP, self.bubble[0])
        w, h = r.width() + 28, r.height() + 20
        bx = (self.W - w) / 2; by = max(4.0, self.H * (1 - self.PADB) - self.char_h() - h - 14)
        return QRectF(bx, by, w, h), r

    # ---------------- overhead UI: emote balloon + reload gauge ----------------
    def emote_pos(self):
        s = math.sqrt(max(0.4, self.cfg["scale"]))
        x = clamp(self.head_px[0] + self.W * 0.15, 30 * s, self.W - 30 * s)
        y = clamp(self.head_px[1] - self.H * 0.07, 28 * s, self.H - 28 * s)
        return x, y, s

    def emote_geom(self):
        if not self.emo: return None
        x, y, s = self.emote_pos(); r = 34 * s
        return QRectF(x - r, y - r, r * 2, r * 2)

    def reload_frac(self):
        """0..1 progress of the reload she is doing right now, else None."""
        a = self.atk
        in_atk = a is not None and a.get("phase") == "reload"
        in_shot = self.state == "shot" and self.shot_key == "reload"
        if not (in_atk or in_shot): return None
        c = self.rig.clip; rc = self.clips.get("reload")
        if c is not None and rc is not None and c is rc and c.duration > 0:
            return clamp(self.rig.t / c.duration, 0.0, 1.0)
        if in_atk:
            tot = max(0.1, float(a.get("reload_total", 2.0)))
            return clamp(1.0 - a["reload_t"] / tot, 0.0, 1.0)
        return None

    def reload_pos(self):
        s = math.sqrt(max(0.4, self.cfg["scale"]))
        x = clamp(self.head_px[0] - self.W * 0.16, 26 * s, self.W - 26 * s)
        y = clamp(self.head_px[1] - self.H * 0.03, 26 * s, self.H - 40 * s)
        return x, y, s

    def update_mask(self):
        self.mask_dirty = False
        a = self.alpha
        if a is None: return
        h, w = a.shape
        ys = np.minimum((np.arange(self.H) * h / self.H).astype(int), h - 1)
        xs = np.minimum((np.arange(self.W) * w / self.W).astype(int), w - 1)
        m = a[np.ix_(ys, xs)] > 12
        def dil(x):
            d = x.copy(); d[:, 1:] |= x[:, :-1]; d[:, :-1] |= x[:, 1:]; d[1:] |= x[:-1]; d[:-1] |= x[1:]; return d
        m = dil(dil(m))
        packed = np.packbits(m, axis=1, bitorder="little").tobytes()
        bg = self.bubble_geom()
        eg = self.emote_geom(); rf = self.reload_frac()
        ad = self.anim_debug_panel()[1] if self.cfg.get("anim_debug") else None
        key = (hash(packed), self.grounded, self.warp_alpha() is not None, None if bg is None else (int(bg[0].x()), int(bg[0].y()), int(bg[0].width()), int(bg[0].height())),
               tuple((int(q["x"]), int(q["y"]), q["kind"]) for q in self.parts if q["life"] >= 0),
               None if eg is None else (int(eg.x()), int(eg.y())), rf is not None,
               None if ad is None else (int(ad.x()), int(ad.y()), int(ad.width()), int(ad.height())))
        if key == self.mask_key: return
        self.mask_key = key
        try:
            bm = QtGui.QBitmap.fromData(QSize(self.W, self.H), packed, QtGui.QImage.Format.Format_MonoLSB)
            region = QtGui.QRegion(bm)
            if self.grounded:
                rw = self.W * 0.17; cy = self.H * (1 - self.PADB)
                region = region.united(QtGui.QRegion(QtCore.QRect(int(self.W / 2 - rw), int(cy - rw * 0.24), int(rw * 2), int(rw * 0.48)),
                                                     QtGui.QRegion.RegionType.Ellipse))
            if bg is not None: region = region.united(QtGui.QRegion(bg[0].toAlignedRect().adjusted(-2, -2, 2, 14)))
            if eg is not None: region = region.united(QtGui.QRegion(eg.toAlignedRect()))
            if ad is not None: region = region.united(QtGui.QRegion(ad.toAlignedRect()))
            if rf is not None:
                rx, ry, rs = self.reload_pos(); rr = int(24 * rs)
                region = region.united(QtGui.QRegion(QtCore.QRect(int(rx - rr), int(ry - rr), rr * 2, int(rr * 2 + 18 * rs))))
            wu = self.warp_alpha()
            if wu is not None:                   # teleport column + floor halo paint outside her silhouette
                fx_, fy_, ch_, top_ = self._warp_geom()
                region = region.united(QtGui.QRegion(QtCore.QRect(int(fx_ - self.W * 0.3), int(top_ - ch_ * 0.3),
                                                                  int(self.W * 0.6), int(fy_ - top_ + ch_ * 0.3 + self.W * 0.12))))
            # A3: particles float outside the silhouette, so fold them into the mask too
            for q in self.parts:
                if q["life"] < 0: continue
                if "mask_r" in q: r = int(q["mask_r"]) + 2
                else: r = (int(q["len"] * 0.6) if q["kind"] == "wind" else int(q["size"] * 2.2)) + 2
                region = region.united(QtGui.QRegion(QtCore.QRect(int(q["x"] - r), int(q["y"] - r), r * 2, r * 2),
                                                         QtGui.QRegion.RegionType.Ellipse))
            self.setMask(region)
        except Exception as e:
            print("[warn] mask disabled:", e); self.cfg["click_through"] = False

    # ---------------- painting ----------------
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 0))
        p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
        if self.grounded:                                           # contact shadow
            rw = self.W * 0.21; cy = self.H * (1 - self.PADB)
            p.save(); p.translate(self.W / 2, cy); p.scale(1, 0.22)
            g = QtGui.QRadialGradient(0, 0, rw); g.setColorAt(0, QtGui.QColor(10, 20, 50, 95)); g.setColorAt(1, QtGui.QColor(10, 20, 50, 0))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QBrush(g)); p.drawEllipse(QPointF(0, 0), rw, rw); p.restore()
        fancy = bool(self.cfg.get("ba_vfx", True))
        self.paint_back_fx(p, fancy)                                 # floor rings + EX aura, behind her
        w_u = self.warp_alpha()
        if w_u is not None:
            self.paint_warp_back(p, w_u)                             # light column, behind her
            if self.img is not None: self.paint_warp_body(p, w_u)    # scan-in + blue flash
            self.paint_warp_front(p, w_u)                            # scanline + bloom
        elif self.img is not None: p.drawImage(QRectF(0, 0, self.W, self.H), self.img)
        for q in self.parts:
            if q["life"] < 0 or q["kind"] == "ring": continue
            u = q["life"] / q["max"]
            al = max(0.0, 1 - u ** 2)
            kind = q["kind"]
            if kind in ("sparkle", "shard", "dust", "tap") or (fancy and kind in ("heart", "star", "puff")):
                self.paint_ba_part(p, q, u, al); continue
            p.save(); p.translate(q["x"], q["y"]); p.rotate(q["rot"] * q["life"] * 90)
            if q["kind"] == "heart":
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(255, 110, 160, int(235 * al))); p.drawPath(heart_path(q["size"]))
            elif q["kind"] == "star":
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(140, 210, 255, int(240 * al))); p.drawPath(star_path(q["size"] * 1.2))
            elif q["kind"] == "wind":
                al = math.sin(math.pi * q["life"] / q["max"])          # quick fade in, fade out
                L = q["len"]
                p.rotate(math.degrees(q["ang"]))                       # align with travel direction
                g = QtGui.QLinearGradient(-L, 0, 0, 0)                 # transparent tail -> bright head
                g.setColorAt(0, QtGui.QColor(255, 255, 255, 0))
                g.setColorAt(1, QtGui.QColor(235, 247, 255, int(210 * al)))
                pen = QtGui.QPen(QtGui.QBrush(g), q["size"]); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
                wob = math.sin(q["life"] * 10 + q["ph"]) * 2.0         # slight flutter
                path = QtGui.QPainterPath(QPointF(-L, 0)); path.quadTo(QPointF(-L * 0.5, wob), QPointF(0, 0))
                p.drawPath(path)
            else:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(255, 255, 255, int(110 * al)))
                p.drawEllipse(QPointF(0, 0), q["size"] * (1 + q["life"]), q["size"] * (1 + q["life"]) * 0.7)
            p.restore()
        self.paint_overhead(p)                                       # emote balloon + reload gauge
        bg = self.bubble_geom()
        if bg is not None:
            rect, tr = bg; text, t0, dur = self.bubble
            age = time.time() - t0
            al = clamp(min(age / 0.15, (dur - age) / 0.3), 0, 1)
            p.save(); p.setOpacity(al)
            path = QtGui.QPainterPath(); path.addRoundedRect(rect, 14, 14)
            tail = QtGui.QPainterPath()
            cx = self.W / 2
            tail.addPolygon(QtGui.QPolygonF([QPointF(cx - 9, rect.bottom() - 1), QPointF(cx + 9, rect.bottom() - 1), QPointF(cx, rect.bottom() + 11)]))
            path = path.united(tail)
            p.setPen(QtGui.QPen(QtGui.QColor("#7cbcff"), 2)); p.setBrush(QtGui.QColor(255, 255, 255, 246)); p.drawPath(path)
            p.fillRect(QRectF(rect.x() + 12, rect.y() + 5, rect.width() - 24, 3), QtGui.QColor("#128cff"))
            p.setFont(self.bfont); p.setPen(QtGui.QColor("#18325a"))
            p.drawText(QRectF(rect.x() + 14, rect.y() + 12, tr.width() + 2, tr.height() + 2), TWRAP, text)
            p.restore()
        if self.cfg.get("ragdoll_debug"):
            self.rag_debug_draw(p)
        if self.cfg.get("anim_debug"):
            self.anim_debug_draw(p)
        p.end()

    # ---------------- teleport-in (summon) ----------------
    @staticmethod
    def _sm(a, b, u):
        t = clamp((u - a) / (b - a), 0.0, 1.0); return t * t * (3 - 2 * t)

    def _warp_geom(self):
        fx, fy = self.W / 2, self.H * (1 - self.PADB)
        ch = self.char_h()
        return fx, fy, ch, fy - ch * 1.06

    def _warp_reveal_y(self, u):
        """The scanline: sweeps from her feet to just over her head between u=0.12 and u=0.58."""
        fx, fy, ch, top = self._warp_geom()
        k = self._sm(0.12, 0.58, u)
        return fy + 4 - (fy + 4 - top) * k

    def paint_warp_back(self, p, u):
        fx, fy, ch, top = self._warp_geom()
        # column of light: snaps open, holds while she forms, thins and fades
        a = self._sm(0.0, 0.10, u) * (1.0 - self._sm(0.45, 0.95, u))
        if a <= 0.01: return
        wcol = self.W * (0.16 + 0.05 * self._sm(0.0, 0.15, u)) * (1.0 - 0.55 * self._sm(0.5, 0.95, u))
        ytop = top - ch * 0.25
        p.save(); p.setPen(Qt.PenStyle.NoPen)
        for width, rgb, aa in ((wcol * 1.0, vfx.BA_SKY, 70), (wcol * 0.45, (200, 238, 255), 120), (wcol * 0.14, vfx.WHITE, 170)):
            lg = QtGui.QLinearGradient(fx - width, 0, fx + width, 0)
            lg.setColorAt(0.0, vfx.qc(rgb, 0)); lg.setColorAt(0.5, vfx.qc(rgb, aa * a)); lg.setColorAt(1.0, vfx.qc(rgb, 0))
            p.setBrush(QtGui.QBrush(lg))
            # tapered towards the top so the column has no hard end
            path = QtGui.QPainterPath(QPointF(fx - width, fy))
            path.lineTo(fx - width * 0.55, ytop + ch * 0.25); path.lineTo(fx, ytop)
            path.lineTo(fx + width * 0.55, ytop + ch * 0.25); path.lineTo(fx + width, fy); path.closeSubpath()
            p.drawPath(path)
        # a pale glow pool at her feet
        vfx.glow(p, fx, fy, self.W * 0.26, vfx.BA_SKY, 110 * a)
        # thin turning halo on the floor (BA tactical circle), small and short-lived
        ha = self._sm(0.02, 0.15, u) * (1.0 - self._sm(0.55, 0.9, u))
        if ha > 0.01:
            p.translate(fx, fy); p.scale(1.0, 0.28); p.rotate(u * 140)
            pn = vfx.pen(vfx.BA_SKY, 210 * ha, 3.0); pn.setDashPattern([5, 4]); p.setPen(pn); p.setBrush(vfx.NOBRUSH)
            r = self.W * (0.2 + 0.06 * self._sm(0.0, 0.4, u))
            p.drawEllipse(QPointF(0, 0), r, r)
            p.setPen(vfx.pen(vfx.WHITE, 150 * ha, 1.6)); p.drawEllipse(QPointF(0, 0), r * 0.78, r * 0.78)
        p.restore()

    def paint_warp_body(self, p, u):
        rect = QRectF(0, 0, self.W, self.H)
        ry = self._warp_reveal_y(u)
        # silhouette tinted pale blue, fading as she settles in
        tint = 1.0 - self._sm(0.35, 0.92, u)
        p.save()
        p.setClipRect(QRectF(0, ry, self.W, self.H - ry))
        p.setOpacity(0.35 + 0.65 * self._sm(0.1, 0.55, u))
        p.drawImage(rect, self.img)
        p.restore()
        if tint > 0.01:
            sil = QtGui.QImage(self.img.size(), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
            sil.setDevicePixelRatio(self.img.devicePixelRatio())
            sil.fill(QtGui.QColor(185, 232, 255))
            q = QtGui.QPainter(sil)
            q.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_DestinationIn)
            q.drawImage(QRectF(0, 0, sil.width() / sil.devicePixelRatio(), sil.height() / sil.devicePixelRatio()), self.img)
            q.end()
            p.save()
            p.setClipRect(QRectF(0, ry, self.W, self.H - ry))
            p.setOpacity(0.85 * tint)
            p.drawImage(rect, sil)
            p.restore()

    def paint_warp_front(self, p, u):
        fx, fy, ch, top = self._warp_geom()
        ry = self._warp_reveal_y(u)
        sa = self._sm(0.08, 0.16, u) * (1.0 - self._sm(0.52, 0.66, u))
        p.save(); p.setPen(Qt.PenStyle.NoPen)
        if sa > 0.01:                                   # the scanline drawing her in
            hw = self.W * 0.24
            lg = QtGui.QLinearGradient(fx - hw, 0, fx + hw, 0)
            lg.setColorAt(0.0, vfx.qc(vfx.BA_SKY, 0)); lg.setColorAt(0.5, vfx.qc(vfx.WHITE, 235 * sa)); lg.setColorAt(1.0, vfx.qc(vfx.BA_SKY, 0))
            p.setBrush(QtGui.QBrush(lg)); p.drawRect(QRectF(fx - hw, ry - 1.2, hw * 2, 2.4))
            gl = QtGui.QLinearGradient(0, ry - 14, 0, ry + 14)
            gl.setColorAt(0.0, vfx.qc(vfx.BA_SKY, 0)); gl.setColorAt(0.5, vfx.qc(vfx.BA_SKY, 90 * sa)); gl.setColorAt(1.0, vfx.qc(vfx.BA_SKY, 0))
            p.setBrush(QtGui.QBrush(gl)); p.drawRect(QRectF(fx - hw * 0.8, ry - 14, hw * 1.6, 28))
        # one gentle bloom over her chest as the scan completes
        ba = self._sm(0.5, 0.6, u) * (1.0 - self._sm(0.6, 0.9, u))
        if ba > 0.01:
            vfx.glow(p, fx, fy - ch * 0.55, ch * 0.42, vfx.BA_SKY, 70 * ba)
            vfx.glow(p, fx, fy - ch * 0.55, ch * 0.2, vfx.WHITE, 60 * ba)
        p.restore()

    # ---------------- Blue Archive VFX painting ----------------
    def paint_back_fx(self, p, fancy):
        """Ground rings and the EX aura: drawn before the model so she stands in front of them."""
        for q in self.parts:
            if q["kind"] != "ring" or q["life"] < 0: continue
            vfx.ground_ring(p, q["x"], q["y"], q["life"] / q["max"], q["size"], q.get("col", vfx.BA_SKY), q.get("flat", 0.3))
        if fancy and self.state == "shot" and self.shot_key == "cutin" and not self.ragdoll:
            t = self.state_t if self.state_t > 0 else 0.0
            fx, fy = self.W / 2, self.H * (1 - self.PADB)
            ch = self.char_h(); cy = fy - ch * 0.5
            a = min(1.0, t / 0.25) * (0.75 + 0.25 * math.sin(t * 7.0))
            vfx.glow(p, fx, cy, ch * 0.62, vfx.BA_SKY, 120 * a)
            vfx.glow(p, fx, cy, ch * 0.32, vfx.WHITE, 70 * a)
            p.save(); p.translate(fx, fy); p.scale(1.0, 0.3); p.rotate(t * 90)  # turning halo on the floor
            pn = vfx.pen(vfx.BA_SKY, 200 * a, 4.0); pn.setDashPattern([6, 4]); p.setPen(pn); p.setBrush(vfx.NOBRUSH)
            p.drawEllipse(QPointF(0, 0), self.W * 0.3, self.W * 0.3)
            p.setPen(vfx.pen(vfx.WHITE, 160 * a, 2.0)); p.drawEllipse(QPointF(0, 0), self.W * 0.24, self.W * 0.24)
            p.restore()

    def paint_ba_part(self, p, q, u, al):
        kind = q["kind"]; x, y = q["x"], q["y"]
        if kind == "sparkle" or kind == "star":
            tw = 0.65 + 0.35 * math.sin(q["life"] * 18 + q.get("ph", q["x"]))
            col = q.get("col", vfx.BA_SKY)
            vfx.sparkle(p, x, y, q["size"] * (1.25 if kind == "star" else 1.0) * tw * (1.0 - 0.3 * u), col, 255 * al,
                        q["rot"] * q["life"] * 90)
        elif kind == "shard":
            vfx.shard(p, x, y, q["size"], q.get("col", vfx.WHITE), 255 * al, math.degrees(q["rot"] * q["life"] * 3),
                      outline=q.get("hollow", False))
        elif kind == "heart":
            p.save(); p.translate(x, y); p.rotate(q["rot"] * q["life"] * 60)
            s = q["size"] * (1.0 + 0.12 * math.sin(q["life"] * 12))
            hp = heart_path(s)
            p.strokePath(hp, vfx.pen(vfx.WHITE, 235 * al, 2.4)); p.fillPath(hp, vfx.qc(vfx.BA_PINK, 240 * al))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(vfx.qc(vfx.WHITE, 210 * al))
            p.drawEllipse(QPointF(-s * 0.4, -s * 0.28), s * 0.17, s * 0.11)
            p.restore()
        elif kind in ("dust", "puff"):
            r = q["size"] * (0.8 + 1.1 * vfx.ease_out(u))
            vfx.dust_cloud(p, x, y, r, 235 * (1.0 - u) ** 1.3, q.get("seed", int(q["size"] * 977)))
        elif kind == "tap":
            vfx.tap_fx(p, x, y, u, q["size"], q.get("seed", 0))

    def paint_overhead(self, p):
        if self.emo:
            kind, t0, dur = self.emo
            x, y, s = self.emote_pos()
            vfx.emote(p, kind, x, y, (time.time() - t0) / max(0.1, dur), s)
        rf = self.reload_frac()
        if rf is not None and self.cfg.get("ba_vfx", True):
            x, y, s = self.reload_pos()
            p.save(); p.translate(x, y); p.scale(s, s); vfx.reload_gauge(p, 0, 0, rf); p.restore()

    def rag_debug_draw(self, p):
        """--rag-debug: the physics capsules, joints and window boxes, on top.

        Drawn in WINDOW px over the render, so a limb the physics has not caught
        up with shows up at once as a bone poking outside its capsule.
        """
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.rag3 is not None:                    # 3D engine: joints shaded by depth
            self.rag3_debug_draw(p)
            p.restore()
            return
        sk = self.rag_skel
        if sk is not None:
            p.setPen(QtGui.QPen(QtGui.QColor(0, 255, 140, 200), 1))
            for (a, c, r) in sk.body_ends():
                p.drawLine(QPointF(a[0] - self.px, a[1] - self.py),
                           QPointF(c[0] - self.px, c[1] - self.py))
                p.drawEllipse(QPointF(a[0] - self.px, a[1] - self.py), r, r)
                p.drawEllipse(QPointF(c[0] - self.px, c[1] - self.py), r, r)
            pose = sk.pose()
            p.setPen(QtGui.QPen(QtGui.QColor(255, 60, 60, 230), 1))
            p.drawEllipse(QPointF(pose["x"] - self.px, pose["y"] - self.py), 5, 5)
            p.setPen(QtGui.QPen(QtGui.QColor(255, 210, 0, 200), 1))
            for i, body in enumerate(sk.bodies):
                par = sk.parents[i]
                if par < 0:
                    continue
                pp = sk.bodies[par].position
                p.drawLine(QPointF(pp[0] - self.px, pp[1] - self.py),
                           QPointF(body.position[0] - self.px, body.position[1] - self.py))
        W = self.A.rag_world
        if W is not None:
            p.setPen(QtGui.QPen(QtGui.QColor(120, 180, 255, 110), 1))
            for _h, w in W._wins.items():
                l, t, r_, b = w["rect"]
                p.drawRect(QRectF(l - self.px, t - self.py, r_ - l, b - t))
        p.restore()
