"""Student Select: a Blue Archive style formation screen for picking who goes on the desktop.

    +--------------------------------------------------------------------------------+
    | [<]  Formation                         (Students 9) (Selected 3) (Desktop 2) [x] |
    |      STUDENT SELECT                                                            |
    |  ~~~~~~~~~~~~~~~~~~~~~~ live 3D stage: the formation ~~~~~~~~~~~~~~~~~~~~~~~~~  |
    |        Hina          Shiroko          Ayane          ( + )                     |
    |       (( 1 ))        (( 2 ))         (( 3 ))         (( 4 ))                   |
    |  STUDENTS (9)   [All] [Selected] [On Desktop]                                  |
    |  [card][card][card][card][card][card][card] ...  (wheel / arrows to scroll)    |
    |  [x] Open at startup   [Control Panel]      +1 deploy - 1 recall   [Clear][All][ DEPLOY ] |
    +--------------------------------------------------------------------------------+

The models are the real thing, but only where it pays. PreviewEngine borrows each Character's
own Renderer (the same toon shader the desktop uses), points it at a private off-screen
framebuffer for one draw, and samples the model's Formation_Idle clip. The renderer's camera /
framebuffer state is saved and restored around that draw, so a student who is on the desktop
at the same time is never disturbed. Cold previews share the on-demand model pool.

The roster cards are NOT animated. A card's portrait is frame 0 of that same clip, drawn once
into that same buffer and kept as a flat QImage (Entry.still) - the portrait window cut out of
the shot, no 3D model and no animation behind it. So painting the roster costs one drawImage
per card instead of a framebuffer readback per student per tick, and a student who is not in
the formation holds no live render at all. Only the students standing on the stage animate.

The formation is *staged*: clicking cards only changes the screen. DEPLOY applies it -
students taken out are hidden (App.set_pet_visible, so the panel and the saved settings stay
in step), new ones drop onto the desktop one after another, left to right in formation order.
Students already out stay exactly where they are. DEPLOY does *not* close this screen: it
re-reads the desktop as they land, so it doubles as a progress bar and as the next decision,
and greys out once the formation matches the desktop again.

Everyone on the stage stands the same height in a slot the same width. The stage is one row
of at least STAGE_MIN slots, so it reads the same empty as it does with two students in it;
past that they shrink to fit, and past STAGE_FIT they stop shrinking and the stage scrolls
sideways instead (_stage_scroll_ui draws the track and the arrows), so twenty students stay
readable.

Controls
    click card            add / remove from the formation
    click stage student   remove her;  drag her sideways to turn her around
    wheel over the stage  scroll the formation;  over the cards, scroll the roster
    arrow keys            scroll the roster
    Enter                 deploy          Esc   close
    Ctrl+A                select all      Del   clear
"""
from __future__ import annotations

import math
import random
import time
import traceback

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer

from .portraits import Portraits
from .config import DEFAULTS
from .mathutil import m4, rot_y, sample_clip, translate

# Persisted with the other settings (App only loads keys that exist in DEFAULTS, and this
# module is imported before the App is built).
DEFAULTS.setdefault("picker_on_start", True)

IW, IH = 384, 448                 # off-screen preview size (px), shared by every student
PREVIEW_FPS = 30
PREVIEW_CLIP = "Formation_Idle"
STILL_H = 256                     # ceiling on a stored portrait's height (px)

STAGE_MIN = 5                     # slots on show even when nobody is picked: the row of "+"
STAGE_FIT = 10                    # students the stage holds before it has to scroll
SLOT_W_MAX = 232.0                # a slot never gets wider than this
STAGE_EDGE = 30.0                 # breathing room left and right of the formation

# ---- Blue Archive palette ----
NAVY = QtGui.QColor(35, 57, 91)
NAVY_D = QtGui.QColor(20, 34, 58)
BLUE = QtGui.QColor(30, 161, 255)
BLUE_D = QtGui.QColor(10, 115, 217)
CYAN = QtGui.QColor(111, 224, 255)
SKY = QtGui.QColor(206, 236, 255)
YELLOW = QtGui.QColor(255, 226, 63)
YELLOW_L = QtGui.QColor(255, 243, 150)
YELLOW_D = QtGui.QColor(247, 178, 0)
PINK = QtGui.QColor(255, 143, 199)
ORANGE = QtGui.QColor(255, 140, 66)
RED = QtGui.QColor(255, 84, 104)
GREEN = QtGui.QColor(58, 214, 160)
WHITE = QtGui.QColor(255, 255, 255)
GREY = QtGui.QColor(118, 136, 163)

AF = Qt.AlignmentFlag
NOPEN = Qt.PenStyle.NoPen


# =============================================================================
#  Small helpers
# =============================================================================
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def ease_out(x):
    x = clamp(x, 0.0, 1.0)
    return 1 - (1 - x) ** 3


def ease_back(x):
    x = clamp(x, 0.0, 1.0)
    c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def col(c, a=None):
    q = QtGui.QColor(c)
    if a is not None: q.setAlpha(int(clamp(a, 0, 255)))
    return q


def para(r, s):
    """Parallelogram leaning right (the Blue Archive button shape)."""
    return QtGui.QPolygonF([QPointF(r.left() + s, r.top()), QPointF(r.right(), r.top()),
                            QPointF(r.right() - s, r.bottom()), QPointF(r.left(), r.bottom())])


def poly_path(poly):
    p = QtGui.QPainterPath(); p.addPolygon(poly); p.closeSubpath(); return p


def rounded(r, rad):
    p = QtGui.QPainterPath(); p.addRoundedRect(r, rad, rad); return p


_FONTS = {}


def font(px, weight=QtGui.QFont.Weight.Bold, italic=False, spacing=0.0):
    key = (int(px), weight, italic, spacing)
    f = _FONTS.get(key)
    if f is None:
        f = QtGui.QFont()
        try: f.setFamilies(["Segoe UI", "Noto Sans", "Noto Sans JP", "Yu Gothic UI", "Helvetica Neue", "Arial"])
        except Exception: f.setFamily("Segoe UI")
        f.setPixelSize(max(1, int(px))); f.setWeight(weight); f.setItalic(italic)
        if spacing: f.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, spacing)
        f.setHintingPreference(QtGui.QFont.HintingPreference.PreferNoHinting)
        _FONTS[key] = f
    return f


def text(p, rect, s, px, color, weight=QtGui.QFont.Weight.Bold, italic=False,
         align=AF.AlignCenter, spacing=0.0, elide=False):
    f = font(px, weight, italic, spacing)
    p.setFont(f); p.setPen(color)
    if elide:
        s = QtGui.QFontMetrics(f).elidedText(s, Qt.TextElideMode.ElideRight, int(max(4, rect.width())))
    p.drawText(rect, align, s)


def spinner(p, c, r, now, color=BLUE):
    p.save()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QtGui.QPen(col(color, 60), 3)); p.drawEllipse(c, r, r)
    p.setPen(QtGui.QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), int((-now * 420) % 360 * 16), 100 * 16)
    p.restore()


# =============================================================================
#  Previews: the student's own renderer, one draw into our framebuffer
# =============================================================================
class Entry:
    """One student on the screen: her pet, her character, and her preview state."""

    def __init__(self, slot, portraits):
        self.slot, self.idx, self.name = slot, slot.index, slot.name
        self.ch = slot  # Metadata for CombatProfile; never materializes a model.
        self.file = slot.path.name
        self.portraits = portraits
        self.preview = self.clip = self.last = self.img = None
        self.t = 0.0
        self.still_state = int(portraits.has(slot))
        self.bbox = None                    # (x0, y0, x1, y1) of her pixels in the preview
        self.bbox_n = 0
        self.failed = False
        self.errors = 0
        self.turn = 0.0                     # drag-to-rotate on the stage (radians)
        self.hover = 0.0                    # animated 0..1
        self.sel_t = -10.0                  # when she joined the formation (appear effect)
        h = 0
        for c in self.name: h = (h * 31 + ord(c)) % 360
        self.hue = h

    @property
    def still(self):
        return self.portraits.image(self.slot)

    @still.setter
    def still(self, image):
        self.portraits.save(self.slot, image)

    def drop_preview(self):
        self.preview = self.clip = self.last = self.img = None

    def bb(self):
        return self.bbox or (IW * 0.3, IH * 0.33, IW * 0.7, IH * 0.93)


class PreviewEngine:
    # Everything Renderer.set_fit / set_size / draw writes that the desktop pet relies on.
    KEYS = ("fbo", "fbo_rs", "w", "h", "fit", "fit_scale", "V_world", "dist", "eye", "view",
            "proj", "PV", "world_per_px", "lens_px", "ride_fade")

    # The portrait window a card is cut to, as a fraction of her height: exactly what the
    # cards framed out of the live shot, so a baked portrait reads the same as the old one.
    PORTRAIT_H = 0.54                # the top 54% of her, head to about the waist
    PORTRAIT_ASPECT = 1.06           # width / height - about a card's portrait frame

    def __init__(self, app):
        self.app = app
        self.ctx = None
        self.fbo = self.rs = None
        self.ok = True

    def _alloc(self):
        ctx = self.ctx
        try:
            c = ctx.renderbuffer((IW, IH), 4, samples=4); d = ctx.depth_renderbuffer((IW, IH), samples=4)
            self.fbo = ctx.framebuffer(color_attachments=[c], depth_attachment=d)
        except Exception:
            c = ctx.renderbuffer((IW, IH), 4); d = ctx.depth_renderbuffer((IW, IH))
            self.fbo = ctx.framebuffer(color_attachments=[c], depth_attachment=d)
        self.rs = ctx.framebuffer(color_attachments=[ctx.renderbuffer((IW, IH), 4)])

    @staticmethod
    def clip_for(ch):
        c = None
        try: c = ch.model.find_clip(PREVIEW_CLIP)
        except Exception: c = None
        if c is None:
            clips = getattr(ch, "clips", {}) or {}
            c = clips.get("alert") or clips.get("idle")     # alert = Formation_Idle (or her profile's own)
        return c

    def light(self):
        cfg = self.app.cfg
        az = math.radians(float(cfg.get("light_az", -30.0)))
        el = math.radians(float(cfg.get("light_el", 38.0)))
        return (math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az))

    def render(self, e, now):
        """Advance her Formation_Idle and keep `e.img`, the live shot the stage draws.

        Only ever called for the students standing on the stage; the cards use their
        baked still instead (see bake), so a roster of twenty costs nothing per tick.
        """
        if not self.ok or e.failed: return
        if e.clip is None:
            e.failed = True; return
        dt = 0.0 if e.last is None else min(now - e.last, 0.5)
        e.last = now
        dur = max(float(getattr(e.clip, "duration", 0.0) or 0.0), 1e-6)
        e.t = (e.t + dt) % dur
        try:
            data = self._frame(e, e.t, e.turn)
        except Exception:
            e.errors += 1
            if e.errors == 1:
                print(f"[picker] preview of {e.name} failed:"); traceback.print_exc()
            if e.errors >= 3:
                e.failed = True
            return
        arr = np.frombuffer(data, np.uint8).reshape(IH, IW, 4)[::-1]       # GL rows are bottom-up
        e.img = self._image(arr)
        if e.bbox_n < 24:                     # her silhouette, unioned over the first frames
            e.bbox_n += 1
            a = arr[:, :, 3]
            rows = np.flatnonzero(a.max(axis=1) > 10); cols = np.flatnonzero(a.max(axis=0) > 10)
            if rows.size and cols.size:
                b = (float(cols[0]), float(rows[0]), float(cols[-1] + 1), float(rows[-1] + 1))
                if e.bbox is None: e.bbox = b
                else:
                    o = e.bbox
                    e.bbox = (min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3]))

    # ------------------------------------------------------------------
    #  The roster portrait: frame 0, once
    # ------------------------------------------------------------------
    def bake(self, e):
        """Draw frame 0 of her clip into our buffer once and keep it as `e.still`.

        A card wants a picture, not a performance. The one shot is taken with the same
        borrowed renderer and the same save/restore as the stage, but afterwards only the
        bitmap is kept: no clip sampling, no pose, no framebuffer readback, no 3D model
        held for the card. What is stored is the portrait window (PORTRAIT_H of her height,
        at PORTRAIT_ASPECT, where she stood), cropped out of the shot - not the whole frame.
        """
        if e.still_state: return
        e.still_state = 2                                  # nothing to bake until proven otherwise
        if not self.ok or e.failed or e.clip is None: return
        try:
            data = self._frame(e, 0.0, 0.0)
        except Exception:
            print(f"[picker] portrait of {e.name} failed:"); traceback.print_exc()
            return
        arr = np.frombuffer(data, np.uint8).reshape(IH, IW, 4)[::-1]
        a = arr[:, :, 3]
        rows = np.flatnonzero(a.max(axis=1) > 10); cols = np.flatnonzero(a.max(axis=0) > 10)
        if not (rows.size and cols.size):
            print(f"[picker] {e.name} drew an empty frame - her card shows no portrait.")
            return
        x0, x1 = float(cols[0]), float(cols[-1] + 1)
        y0, y1 = float(rows[0]), float(rows[-1] + 1)
        bh = y1 - y0
        w, h = bh * self.PORTRAIT_H * self.PORTRAIT_ASPECT, bh * self.PORTRAIT_H
        if w > IW: w, h = float(IW), IW / self.PORTRAIT_ASPECT     # never ask for more than the frame
        if h > IH: h, w = float(IH), IH * self.PORTRAIT_ASPECT
        ix = int(round(clamp((x0 + x1) / 2 - w / 2, 0.0, IW - w)))   # centred on her, head room above
        iy = int(round(clamp(y0 - bh * 0.035, 0.0, IH - h)))
        crop = arr[iy:min(IH, iy + int(round(h))), ix:min(IW, ix + int(round(w)))]
        if not crop.shape[0] or not crop.shape[1]: return
        k = min(1.0, STILL_H / max(crop.shape[0], 1))
        e.still = self._image(crop).scaled(max(1, int(round(crop.shape[1] * k))),
                                           max(1, int(round(crop.shape[0] * k))),
                                           Qt.AspectRatioMode.IgnoreAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
        e.still_state = 1

    # ------------------------------------------------------------------
    #  The one draw, shared by both paths
    # ------------------------------------------------------------------
    def _frame(self, e, t, turn):
        """Draw one pose of `e` into our off-screen buffer -> RGBA bytes (or None).

        Everything Renderer.draw / set_fit writes that the desktop pet relies on is put
        back afterwards, so a student who is on the desktop at the same time never notices
        being photographed.
        """
        if self.fbo is None:
            self.ctx = self.app.ctx
            self._alloc()
        ch = e.preview; r = ch.renderer; m = ch.model
        saved = {k: getattr(r, k) for k in self.KEYS if hasattr(r, k)}
        has_nw = hasattr(m, "node_w"); nw = getattr(m, "node_w", None)
        try:
            T, Q, S, _off, wt = sample_clip(e.clip, t, True)
            world = m.pose_world(T, Q, S)
            fit = ch.fit
            c = np.array([float(fit["cx"]), 0.0, float(fit["cz"])])
            yaw = float(getattr(m, "face_yaw", 0.0) or 0.0) + turn
            M = translate(c) @ m4(rot_y(yaw)) @ translate(-c)
            r.fbo, r.fbo_rs = self.fbo, self.rs
            r.w, r.h = IW, IH
            if hasattr(r, "ride_fade") and getattr(ch, "ride_win", None) is not None:
                r.ride_fade = 0.0                       # no vehicle in the formation shot
            r.set_fit(fit, 1.0)
            if has_nw and wt is not None: m.node_w = wt  # mouth / face shapes of this frame
            # draw() already resolves MSAA and returns the pixels. Reading them
            # again introduced a second synchronous GPU readback per preview.
            return r.draw(world, M, self.light(), self.app.cfg)
        finally:
            for k, v in saved.items():
                try: setattr(r, k, v)
                except Exception: pass
            if has_nw: m.node_w = nw

    @staticmethod
    def _image(arr):
        """Bottom-up GL pixels -> an owned premultiplied RGBA QImage."""
        arr = np.ascontiguousarray(arr)
        h, w = arr.shape[0], arr.shape[1]
        return QtGui.QImage(arr.data, w, h, w * 4,
                            QtGui.QImage.Format.Format_RGBA8888_Premultiplied).copy()


# =============================================================================
#  The formation screen
# =============================================================================
class StudentPicker(QtWidgets.QWidget):
    MARGIN = 18
    BASE_W, BASE_H = 1240, 790
    TOP_H, BOTTOM_H, RHEAD_H = 66, 78, 42

    def __init__(self, app):
        flags = (Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowTitle("Student Select - Desktop Pets")
        self.app = app
        self.engine = PreviewEngine(app)
        self.portraits = Portraits()
        self.entries = [Entry(s, self.portraits) for s in app.roster]
        self._previewing = set()
        self._visible_cards = ()
        self._prepared = {}
        self._deployment = 0
        self.team = []
        self.filter = "all"
        self.scroll = self.scroll_target = self.max_scroll = 0.0
        self.stage_scroll = self.stage_scroll_target = self.stage_max_scroll = 0.0
        self.stage_unit = SLOT_W_MAX
        self.stage_live = frozenset()          # who _tick animates: whoever is on the stage
        self.card_step = 160.0
        self.hits = []
        self.hover = None
        self.press = None
        self.drag_origin = None
        self.rot_drag = None
        self.t_open = self.last_tick = time.perf_counter()
        self.startup = False
        self._closing = False
        self._pending = 0
        self._last_raise = 0.0
        rnd = random.Random(11)
        self.shapes = [(rnd.random(), rnd.random(), rnd.uniform(10, 44), rnd.uniform(0.004, 0.016),
                        rnd.uniform(0, 360), rnd.uniform(-28, 28), rnd.randint(16, 46)) for _ in range(22)]
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.fade = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self.fade.finished.connect(self._fade_done)
        try:
            tray = getattr(app, "tray", None)
            if tray is not None: self.setWindowIcon(tray.icon())
        except Exception:
            pass
        app.qapp.aboutToQuit.connect(self.timer.stop)
        self._wrap_restack()

    # ------------------------------------------------------------------
    #  Living alongside the pets
    # ------------------------------------------------------------------
    def _wrap_restack(self):
        """Pets are always-on-top windows that App re-raises a couple of times a second.
        Raise the formation screen right after them (rate-limited) so it stays in front."""
        app = self.app
        orig = app.restack

        def restack(force=False, _orig=orig):
            # Students stay ABOVE the formation screen: whenever the screen is (re)raised,
            # the pets are raised again right after it so they are never hidden behind it.
            # Their windows are click-through outside their silhouettes, so the screen
            # underneath still gets every click that does not land on a student.
            if self.isVisible() and not self._closing:
                now = time.perf_counter()
                if force or now - self._last_raise > 0.4:
                    self._last_raise = now
                    self.raise_()
                    _orig(True)
                    return
            _orig(force)
        app.restack = restack

    def closing(self):
        return self._closing

    # ------------------------------------------------------------------
    #  Open / close
    # ------------------------------------------------------------------
    def open(self, team=None, startup=False):
        self.startup = startup
        if not self.isVisible() or self._closing:
            self._sync_team(team)
            self._place()
            self._closing = False
            self.t_open = self.last_tick = time.perf_counter()
            self.scroll = self.scroll_target = 0.0
            self.stage_scroll = self.stage_scroll_target = 0.0
            self.hover = None; self.press = None; self.drag_origin = None; self.rot_drag = None
            self.fade.stop()
            self.setWindowOpacity(0.0)
            self.show()
            self.fade.setDuration(280); self.fade.setStartValue(0.0); self.fade.setEndValue(1.0); self.fade.start()
            self.timer.start(int(1000 / PREVIEW_FPS))
            self._tick()
        self.raise_(); self.activateWindow(); self.setFocus()
        try: self.app.restack(force=True)               # students back on top of the screen
        except Exception: pass

    def _sync_team(self, team):
        if team is not None:
            ids = {id(p) for p in team}
            self.team = [e for e in self.entries if id(e.slot) in ids]
            return
        vis = [e for e in self.entries if e.slot.isVisible()]
        if vis:
            self.team = vis
        else:                                # all hidden (Hide all to tray): offer last saved team
            hidden = set(self.app.cfg.get("hidden") or [])
            self.team = [e for e in self.entries if e.name not in hidden]
        now = time.perf_counter()
        for e in self.team: e.sel_t = now - 0.15

    def _place(self):
        try: g = self.app.resolve_screen()
        except Exception: g = self.app.qapp.primaryScreen().availableGeometry()
        w = int(min(self.BASE_W, g.width() * 0.95)); h = int(min(self.BASE_H, g.height() * 0.94))
        self.resize(w, h)
        self.move(g.center().x() - w // 2, g.center().y() - h // 2)

    def close_animated(self):
        if not self.isVisible() or self._closing: return
        self._closing = True
        self.fade.stop()
        self.fade.setDuration(180); self.fade.setStartValue(self.windowOpacity()); self.fade.setEndValue(0.0)
        self.fade.start()

    def _fade_done(self):
        if self._closing:
            self._closing = False
            self.hide()

    def closeEvent(self, ev):                 # Alt+F4 etc: animate instead of destroying
        ev.ignore()
        self.close_animated()

    def hideEvent(self, ev):
        self.timer.stop()
        if not self.app.loading_models: self.app.models.abandon()
        for e in self._previewing: e.drop_preview()
        self._previewing.clear()
        self.stage_live = frozenset()
        super().hideEvent(ev)
        QTimer.singleShot(0, self._after_close)

    def _after_close(self):
        """Nothing to say on the way out any more. One case still needs handling: with no tray
        icon and nobody left on the desktop, closing here would leave the app unreachable, so
        the Control Panel steps in instead."""
        app = self.app
        if self.isVisible() or self._pending or any(p.isVisible() for p in app.pets): return
        if getattr(app, "tray", None) is None:
            try: app.show_panel()
            except Exception: traceback.print_exc()

    # ------------------------------------------------------------------
    #  Formation edits
    # ------------------------------------------------------------------
    def toggle(self, e):
        if e in self.team:
            self.team.remove(e)
        else:
            e.failed = False
            self.app.models.errors.pop(e.slot, None)
            self.team.append(e); e.sel_t = time.perf_counter()
            self._reveal_stage()

    def _reveal_stage(self):
        """Scroll the formation to the student just added, or the ones she hid behind.

        Only matters once the row overflows; the layout it reads is the last one painted,
        which is a frame or two old at worst and is re-clamped every tick.
        """
        if self.stage_max_scroll <= 0 or not self.team:
            return
        R = getattr(self, "stage_R", None)
        if R is None: return
        x = R.left() + STAGE_EDGE + self.stage_unit * (len(self.team) - 0.5)
        if x > R.right() - STAGE_EDGE:
            self.stage_scroll_target = self.stage_max_scroll
        elif x < R.left() + STAGE_EDGE:
            self.stage_scroll_target = 0.0

    def select_all(self):
        now = time.perf_counter()
        for e in self.entries:
            if e not in self.team:
                self.team.append(e); e.sel_t = now
        self.update()

    def clear(self):
        self.team = []; self.update()

    def diff(self):
        selected = {e.slot for e in self.team}
        adds = sum(1 for e in self.team if not e.slot.isVisible())
        rec = sum(1 for p in self.app.pets if p.isVisible() and p.slot not in selected)
        return adds, rec

    def deploy_state(self):
        """What the DEPLOY button should say and whether it should be live.

        Returns (verb, count, enabled). `count` is the number in the headline -
        DEPLOY 2 students, RECALL 1 student. It is disabled while students are still
        dropping in, and whenever a click would change nothing at all, which is the state
        you land in after deploying: the screen stays open now, so the button greys out
        instead of inviting a second click that has no work to do.
        """
        adds, rec = self.diff()
        if self._pending:
            return "DEPLOYING", self._pending, False
        if adds:
            return "DEPLOY", adds, True
        if rec:
            return "RECALL", rec, True
        return "NO CHANGES", 0, False

    def quick_deploy(self, entries=None):
        """Deploy without the screen (tray: 'Deploy everyone')."""
        self.team = list(self.entries if entries is None else entries)
        self.deploy()

    def cancel_deployment(self):
        self._deployment += 1
        self._pending = 0
        self._prepared.clear()

    def deploy(self):
        """Apply the formation. The screen stays open: it re-reads the desktop as the
        students land, so DEPLOY doubles as a progress bar and as the next decision."""
        if not self.deploy_state()[2]:
            return
        if len(self.team) > 12 and any(not e.slot.isVisible() for e in self.team):
            answer = QtWidgets.QMessageBox.question(self, "Deploy a large formation?",
                f"Deploy {len(self.team)} desktop pets? Each active pet needs CPU and GPU memory. "
                "A large library does not need to be deployed all at once.")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes: return
        app = self.app
        want = list(self.team)
        ready = app.prepare_models([e.slot for e in want if not e.slot.isVisible()])
        if ready is None: return
        self._prepared = ready
        self._deployment += 1
        ticket = self._deployment
        order = {e: i for i, e in enumerate(want)}
        for e in self.entries:                          # recall first
            if e not in order and e.slot.isVisible():
                try: app.set_pet_visible(e.slot, False)
                except Exception: traceback.print_exc()
        new = [e for e in want if not e.slot.isVisible()]
        n = len(want)
        self._pending = len(new)
        for j, e in enumerate(new):
            frac = (order[e] + 1) / (n + 1)
            QTimer.singleShot(90 + j * 170, lambda e=e, frac=frac: self._drop_in(e, frac, ticket))
        if not new:
            self._after_deploy()
        self.update()

    def _drop_in(self, e, frac, ticket):
        if ticket != self._deployment: return
        app = self.app
        self._pending = max(0, self._pending - 1)
        if not e.slot.isVisible():
            try:
                p = app.ensure_pet(e.slot, self._prepared)
                g = app.resolve_screen()
                fx = g.left() + (g.right() - g.left()) * frac
                p.teleport_in(fx)                       # stands on the floor, materialises in light
                app.set_pet_visible(p, True)
                try: p.recall()
                except Exception: pass
                app.restack(force=True)                 # above the Student Select window
            except Exception:
                traceback.print_exc()
        if self._pending == 0:
            self._after_deploy()

    def _after_deploy(self):
        self._prepared.clear()
        app = self.app
        app.cfg["deployed"] = [s.name for s in app.roster if s.isVisible()]
        app.cfg["hidden"] = [s.name for s in app.roster if not s.isVisible()]
        try:
            if app.active is None or not app.active.isVisible():
                vis = [e.slot.pet for e in self.team if e.slot.isVisible()]
                if vis: app.set_active(vis[0])
            app.restack(force=True)
            if getattr(app.panel, "sync_char_rows", None): app.panel.sync_char_rows()
            app.save_timer.start(600)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------------
    #  Frame loop
    # ------------------------------------------------------------------
    def _filtered(self):
        if self.filter == "selected": return list(self.team)
        if self.filter == "desktop": return [e for e in self.entries if e.slot.isVisible()]
        return self.entries

    def _tick(self):
        now = time.perf_counter()
        dt = clamp(now - self.last_tick, 0.0, 0.1); self.last_tick = now
        if not self.isVisible() or self.isMinimized() or self._closing: return
        self.scroll_target = clamp(self.scroll_target, 0.0, self.max_scroll)
        prev_scroll = self.scroll + self.stage_scroll
        self.scroll += (self.scroll_target - self.scroll) * (1 - math.exp(-dt * 14))
        self.stage_scroll_target = clamp(self.stage_scroll_target, 0.0, self.stage_max_scroll)
        self.stage_scroll += (self.stage_scroll_target - self.stage_scroll) * (1 - math.exp(-dt * 14))
        if (abs(self.scroll + self.stage_scroll - prev_scroll) > 0.3 and self.rot_drag is None
                and self.drag_origin is None and self.underMouse()):
            lp = self.mapFromGlobal(QtGui.QCursor.pos())
            self._set_hover(self._hit(QPointF(lp.x(), lp.y())))
        hov = self.hover
        mix, decay = 1 - math.exp(-dt * 16), math.exp(-dt * 1.4)
        for e in self.stage_live.union(self._visible_cards):
            tgt = 1.0 if (hov is not None and hov[0] == "card" and hov[1] is e) else 0.0
            e.hover += (tgt - e.hover) * mix
            if self.rot_drag is None or self.rot_drag[0] is not e:
                e.turn *= decay
        live = self.stage_live.intersection(self.team)
        for e in self._previewing - live:
            e.drop_preview()
        self._previewing = set(live)
        requested = False
        for e in sorted(live, key=lambda item: item.idx):
            if e.failed:
                continue
            if e.preview is None:
                if requested or self.app.loading_models:
                    continue
                requested = True
                try:
                    ch = self.app.models.poll(e.slot)
                    if ch is None: continue
                    e.preview = ch
                    e.clip = self.engine.clip_for(ch)
                    if not e.still_state:
                        self.engine.bake(e)
                except Exception:
                    e.failed = True; traceback.print_exc(); continue
            self.engine.render(e, now)
        self.update()

    # ------------------------------------------------------------------
    #  Painting
    # ------------------------------------------------------------------
    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.SmoothPixmapTransform
                         | QtGui.QPainter.RenderHint.TextAntialiasing)
        now = time.perf_counter()
        self.hits = []
        M = self.MARGIN
        R = QRectF(self.rect()).adjusted(M, M, -M, -M)
        # soft drop shadow
        p.setPen(NOPEN)
        for i in range(M, 0, -2):
            p.setBrush(col(NAVY_D, 3 + (M - i) * 0.9))
            p.drawRoundedRect(R.adjusted(-i, -i + 4, i, i + 4), 18 + i, 18 + i)
        frame = rounded(R, 16)
        p.setClipPath(frame)
        top = QRectF(R.left(), R.top(), R.width(), self.TOP_H)
        bottom = QRectF(R.left(), R.bottom() - self.BOTTOM_H, R.width(), self.BOTTOM_H)
        mid = bottom.top() - top.bottom()
        stage = QRectF(R.left(), top.bottom(), R.width(), mid * 0.58)
        rhead = QRectF(R.left(), stage.bottom(), R.width(), self.RHEAD_H)
        roster = QRectF(R.left(), rhead.bottom(), R.width(), bottom.top() - rhead.bottom())
        try:
            self._paint_bg(p, R, now)
            self._paint_stage(p, stage, now)
            self._paint_roster_head(p, rhead, now)
            self._paint_roster(p, roster, now)
            self._paint_bottom(p, bottom, now)
            self._paint_top(p, top, now)
        except Exception:
            traceback.print_exc()
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QtGui.QPen(col(WHITE, 220), 1.5)); p.drawPath(frame)
        p.setPen(QtGui.QPen(col(BLUE, 70), 1)); p.drawRoundedRect(R.adjusted(-1, -1, 1, 1), 17, 17)
        p.end()

    # ---- background ----
    def _paint_bg(self, p, R, now):
        g = QtGui.QLinearGradient(R.topLeft(), R.bottomRight())
        g.setColorAt(0, QtGui.QColor(247, 252, 255)); g.setColorAt(0.55, QtGui.QColor(233, 246, 255))
        g.setColorAt(1, QtGui.QColor(219, 238, 253))
        p.fillRect(R, g)
        p.setPen(QtGui.QPen(col(BLUE, 13), 1))
        step = 22.0; off = (now * 9) % step
        x = R.left() - R.height() + off
        while x < R.right():
            p.drawLine(QPointF(x, R.bottom()), QPointF(x + R.height(), R.top())); x += step
        for xf, yf, sz, sp, rot, rs, a in self.shapes:
            y = R.top() - 40 + ((yf - now * sp) % 1.0) * (R.height() + 80)
            x = R.left() + xf * R.width()
            p.save(); p.translate(x, y); p.rotate(rot + now * rs)
            tri = QtGui.QPolygonF([QPointF(0, -sz * 0.6), QPointF(sz * 0.52, sz * 0.3), QPointF(-sz * 0.52, sz * 0.3)])
            p.setPen(QtGui.QPen(col(BLUE, a), 1.3)); p.setBrush(col(CYAN, a * 0.35)); p.drawPolygon(tri)
            p.restore()

    # ---- top bar ----
    def _paint_top(self, p, T, now):
        g = QtGui.QLinearGradient(T.topLeft(), T.bottomLeft())
        g.setColorAt(0, col(WHITE, 250)); g.setColorAt(1, col(WHITE, 228))
        p.fillRect(T, g)
        p.fillRect(QRectF(T.left(), T.bottom() - 2, T.width(), 2), col(BLUE, 70))
        self.hits.append((QRectF(T), ("drag",)))
        # back
        b = QRectF(T.left() + 16, T.top() + 13, 60, T.height() - 26)
        hov = self.hover == ("back",)
        path = poly_path(para(b, 10))
        p.setPen(NOPEN); p.setBrush(NAVY if hov else BLUE); p.drawPath(path)
        p.setPen(QtGui.QPen(WHITE, 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        cx, cy = b.center().x() - 1, b.center().y()
        p.drawPolyline(QtGui.QPolygonF([QPointF(cx + 5, cy - 9), QPointF(cx - 5, cy), QPointF(cx + 5, cy + 9)]))
        self.hits.append((path, ("back",)))
        # title
        x = b.right() + 20
        p.setPen(NOPEN); p.setBrush(YELLOW)
        p.drawPolygon(para(QRectF(x, T.top() + 15, 9, T.height() - 30), 4))
        text(p, QRectF(x + 18, T.top() + 4, 360, 38), "Formation", 29, NAVY, QtGui.QFont.Weight.Black, True,
             AF.AlignLeft | AF.AlignVCenter)
        text(p, QRectF(x + 21, T.top() + 40, 360, 16), "STUDENT SELECT  \u00b7  \u7de8\u6210", 10, BLUE_D,
             QtGui.QFont.Weight.Bold, False, AF.AlignLeft | AF.AlignVCenter, 2.6)
        # close
        xr = T.right() - 16
        cb = QRectF(xr - 42, T.top() + 14, 42, T.height() - 28)
        hov = self.hover == ("close",)
        p.setPen(QtGui.QPen(col(RED if hov else NAVY, 90 if not hov else 255), 1.2))
        p.setBrush(RED if hov else col(WHITE, 240)); p.drawRoundedRect(cb, 8, 8)
        p.setPen(QtGui.QPen(WHITE if hov else NAVY, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        c = cb.center()
        p.drawLine(QPointF(c.x() - 7, c.y() - 7), QPointF(c.x() + 7, c.y() + 7))
        p.drawLine(QPointF(c.x() + 7, c.y() - 7), QPointF(c.x() - 7, c.y() + 7))
        self.hits.append((QRectF(cb), ("close",)))
        xr = cb.left() - 14
        n_on = sum(1 for e in self.entries if e.slot.isVisible())
        pills = (("\u25cf", "On Desktop", n_on, GREEN), ("\u2713", "Selected", len(self.team), BLUE),
                 ("\u2605", "Students", len(self.entries), PINK))
        for glyph, label, val, c_ in pills:
            w = 152.0
            r = QRectF(xr - w, T.top() + 15, w, T.height() - 30)
            if r.left() < x + 300: break
            p.setPen(QtGui.QPen(col(BLUE, 60), 1)); p.setBrush(col(WHITE, 250))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            d = r.height() - 8
            ic = QRectF(r.left() + 4, r.top() + 4, d, d)
            p.setPen(NOPEN); p.setBrush(c_); p.drawEllipse(ic)
            text(p, ic, glyph, d * 0.55, WHITE, QtGui.QFont.Weight.Black)
            text(p, QRectF(ic.right() + 8, r.top(), 80, r.height()), label, 11, GREY, QtGui.QFont.Weight.Bold,
                 False, AF.AlignLeft | AF.AlignVCenter)
            text(p, QRectF(r.left(), r.top(), r.width() - 14, r.height()), str(val), 17, NAVY,
                 QtGui.QFont.Weight.Black, True, AF.AlignRight | AF.AlignVCenter)
            xr -= w + 10

    # ---- stage ----
    def _paint_stage(self, p, S, now):
        self.stage_R = QRectF(S)
        p.save(); p.setClipRect(S, Qt.ClipOperation.IntersectClip)
        g = QtGui.QLinearGradient(S.topLeft(), S.bottomLeft())
        g.setColorAt(0, QtGui.QColor(196, 232, 255)); g.setColorAt(0.6, QtGui.QColor(234, 247, 255))
        g.setColorAt(1, QtGui.QColor(212, 235, 252))
        p.fillRect(S, g)
        cx = S.center().x()
        floor_y = S.bottom() - 66
        horizon = S.top() + S.height() * 0.6
        # floor + perspective grid
        fl = QRectF(S.left(), horizon, S.width(), S.bottom() - horizon)
        fg = QtGui.QLinearGradient(fl.topLeft(), fl.bottomLeft())
        fg.setColorAt(0, col(WHITE, 0)); fg.setColorAt(0.35, col(WHITE, 170)); fg.setColorAt(1, col(SKY, 200))
        p.fillRect(fl, fg)
        vp = QPointF(cx, S.top() - S.height() * 0.25)
        p.setPen(QtGui.QPen(col(BLUE, 30), 1))
        for i in range(-14, 15):
            xb = cx + i * S.width() / 11
            t = (horizon - S.bottom()) / (vp.y() - S.bottom())
            p.drawLine(QPointF(xb, S.bottom()), QPointF(xb + (vp.x() - xb) * t, horizon))
        for j in range(1, 8):
            y = horizon + (S.bottom() - horizon) * (j / 7.0) ** 1.7
            p.drawLine(QPointF(S.left(), y), QPointF(S.right(), y))
        # halo / magic circle
        hc = QPointF(cx, S.top() + S.height() * 0.44)
        rad = S.height() * 0.5
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QtGui.QPen(col(WHITE, 150), 2)); p.drawEllipse(hc, rad, rad)
        p.setPen(QtGui.QPen(col(BLUE, 40), 1.2)); p.drawEllipse(hc, rad * 0.82, rad * 0.82)
        pen = QtGui.QPen(col(BLUE, 55), 3); pen.setDashPattern([2, 6]); pen.setDashOffset(now * 6)
        p.setPen(pen); p.drawEllipse(hc, rad * 0.92, rad * 0.92)
        p.setPen(QtGui.QPen(col(PINK, 90), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        arc = QRectF(hc.x() - rad * 1.06, hc.y() - rad * 1.06, rad * 2.12, rad * 2.12)
        a0 = int((now * 22) % 360 * 16)
        p.drawArc(arc, a0, 40 * 16); p.drawArc(arc, a0 + 180 * 16, 40 * 16)
        p.setPen(QtGui.QPen(col(CYAN, 140), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        arc2 = QRectF(hc.x() - rad * 0.74, hc.y() - rad * 0.74, rad * 1.48, rad * 1.48)
        a1 = int((-now * 35) % 360 * 16)
        p.drawArc(arc2, a1, 70 * 16); p.drawArc(arc2, a1 + 180 * 16, 70 * 16)
        # watermark
        text(p, QRectF(S.left() + 26, S.top() + 6, S.width(), 110), "FORMATION", 96, col(BLUE, 20),
             QtGui.QFont.Weight.Black, True, AF.AlignLeft | AF.AlignTop)
        text(p, QRectF(S.left() + 30, S.top() + 104, 400, 20), "SCHALE  \u00b7  DESKTOP DEPLOYMENT", 11,
             col(BLUE_D, 110), QtGui.QFont.Weight.Bold, False, AF.AlignLeft | AF.AlignTop, 3)
        # --- the formation -------------------------------------------------------
        # The stage is one row of slots, and it always shows at least STAGE_MIN of them - so
        # it reads the same empty as it does with two students in it. Up to STAGE_MIN they
        # stay a comfortable size; from there they shrink to fit, and past STAGE_FIT they
        # stop shrinking and the stage scrolls instead. One width and one height for all of
        # them, so nobody ends up taller than her neighbour.
        team = self.team
        n = len(team)
        room = max(S.width() - STAGE_EDGE * 2, 60.0)
        tight = room / STAGE_FIT                      # ten across is as small as a slot gets
        slot_w = max(tight, min(SLOT_W_MAX, room / max(n, STAGE_MIN)))
        unit = slot_w                                 # slots sit centre to centre, no gaps
        self.stage_unit = unit
        self.stage_max_scroll = max(0.0, unit * n - room)
        self.stage_scroll_target = clamp(self.stage_scroll_target, 0.0, self.stage_max_scroll)
        self.stage_scroll = clamp(self.stage_scroll, 0.0, self.stage_max_scroll)
        if self.stage_max_scroll > 0:
            first = min(int(self.stage_scroll // unit), max(0, n - 1))
            count = min(int(math.ceil(room / unit)) + 1, max(n - first, 1) + 1)
            left = S.left() + STAGE_EDGE - self.stage_scroll
        else:
            # It is the row that gets centred, not the students in it: centring them
            # instead lets the trailing "+" slots drag the whole thing off to one side.
            first, count = 0, max(STAGE_MIN, n)
            left = S.left() + STAGE_EDGE + (room - unit * count) / 2.0
        h = (floor_y - S.top() - 18.0) * slot_w / SLOT_W_MAX    # she fills her slot as far as she can
        for i in range(first, first + count):
            self._platform(p, left + unit * (i + 0.5), floor_y, slot_w, i, i < n, now)
        rotating = self.rot_drag[0] if self.rot_drag else None
        lo, hi = S.left() - slot_w * 0.7, S.right() + slot_w * 0.7
        on = [(i, left + unit * (i + 0.5)) for i in range(max(0, first-1), min(n, first+count+1))]
        on = [(i, c) for i, c in on if lo <= c <= hi]         # skip whoever is scrolled off
        # ... and _tick only animates these, plus a slot either side so nothing pops in
        # as a spinner while the row is being scrolled.
        self.stage_live = frozenset(team[i] for i in range(max(0, first - 1), min(n, first + count + 1)))
        for i, c in on:
            self._stage_model(p, team[i], c, floor_y, slot_w, h, S, now, rotating)
        for i, c in on:
            self._name_plate(p, team[i], c, floor_y, slot_w, i)
        if n == 0:
            text(p, QRectF(S.left(), S.top() + S.height() * 0.24, S.width(), 34),
                 "Tap a student below to add her to the formation", 19, col(NAVY, 200),
                 QtGui.QFont.Weight.Black, True)
            text(p, QRectF(S.left(), S.top() + S.height() * 0.24 + 34, S.width(), 22),
                 "Every .glb in model/ is a student \u00b7 DEPLOY sends the formation to your desktop", 12,
                 col(GREY, 230), QtGui.QFont.Weight.Bold)
        # edge fades + arrows, and the scrollbar along the top (the name plates own the floor)
        self._stage_scroll_ui(p, S, floor_y, room, now)
        p.restore()

    def _stage_scroll_ui(self, p, S, floor_y, room, now):
        """The formation's own scroller: edge arrows when there is somewhere to go, and a
        track across the top once the row is wider than the stage."""
        if self.stage_max_scroll <= 0:
            return
        for side in (-1, 1):
            can = self.stage_scroll > 1 if side < 0 else self.stage_scroll < self.stage_max_scroll - 1
            if not can: continue
            w = 64.0
            er = QRectF(S.left() if side < 0 else S.right() - w, S.top(), w, floor_y - S.top() - 6)
            fg = QtGui.QLinearGradient(er.topLeft(), er.topRight())
            a0, a1 = col(QtGui.QColor(198, 232, 255), 235), col(QtGui.QColor(198, 232, 255), 0)
            fg.setColorAt(0, a0 if side < 0 else a1); fg.setColorAt(1, a1 if side < 0 else a0)
            p.fillRect(er, fg)
            bc = QPointF(er.left() + 24 if side < 0 else er.right() - 24, er.center().y())
            hov = self.hover == ("stage_arrow", side)
            p.setPen(QtGui.QPen(col(BLUE, 120), 1)); p.setBrush(BLUE if hov else col(WHITE, 245))
            p.drawEllipse(bc, 18, 18)
            p.setPen(QtGui.QPen(WHITE if hov else BLUE, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            p.drawPolyline(QtGui.QPolygonF([QPointF(bc.x() - 3 * side, bc.y() - 8),
                                            QPointF(bc.x() + 4 * side, bc.y()),
                                            QPointF(bc.x() - 3 * side, bc.y() + 8)]))
            self.hits.append((QRectF(bc.x() - 18, bc.y() - 18, 36, 36), ("stage_arrow", side)))
        track = QRectF(S.left() + STAGE_EDGE, S.top() + 7, room, 5)
        p.setPen(NOPEN); p.setBrush(col(WHITE, 150)); p.drawRoundedRect(track, 2.5, 2.5)
        tw = max(48.0, track.width() * room / max(self.stage_max_scroll + room, 1.0))
        tx = track.left() + (track.width() - tw) * (self.stage_scroll / self.stage_max_scroll)
        p.setPen(QtGui.QPen(col(WHITE, 190), 1)); p.setBrush(BLUE)
        p.drawRoundedRect(QRectF(tx, track.top(), tw, 5), 2.5, 2.5)
        a = int(self.stage_scroll // self.stage_unit) + 1
        b = min(len(self.team), int(math.ceil((self.stage_scroll + room) / self.stage_unit)))
        text(p, QRectF(track.left(), track.bottom() + 3, track.width(), 14),
             f"formation  {a}\u2013{b} of {len(self.team)}", 9, col(NAVY, 140),
             QtGui.QFont.Weight.Black, True, AF.AlignRight | AF.AlignVCenter, 1.2)

    def _platform(self, p, cx, fy, slot_w, i, filled, now):
        rx = min(slot_w * 0.38, 80.0); ry = rx * 0.3
        c = QPointF(cx, fy)
        rg = QtGui.QRadialGradient(c, rx * 1.6)
        rg.setColorAt(0, col(CYAN, 150 if filled else 50)); rg.setColorAt(1, col(CYAN, 0))
        p.setPen(NOPEN); p.setBrush(rg)
        p.save(); p.translate(c); p.scale(1.0, 0.32); p.drawEllipse(QPointF(0, 0), rx * 1.6, rx * 1.6); p.restore()
        dg = QtGui.QLinearGradient(QPointF(cx, fy - ry), QPointF(cx, fy + ry))
        dg.setColorAt(0, col(WHITE, 240)); dg.setColorAt(1, col(BLUE, 110 if filled else 40))
        p.setBrush(dg); p.setPen(QtGui.QPen(col(BLUE, 210 if filled else 90), 2))
        p.drawEllipse(c, rx, ry)
        pen = QtGui.QPen(col(BLUE_D if filled else BLUE, 150 if filled else 70), 1.5)
        pen.setDashPattern([3, 4]); pen.setDashOffset(-now * 10 if filled else 0)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush); p.drawEllipse(c, rx * 0.7, ry * 0.7)
        tag = QRectF(cx - rx - 8, fy + ry - 9, 30, 19)
        p.setPen(NOPEN); p.setBrush(NAVY if filled else col(GREY, 150)); p.drawPolygon(para(tag, 6))
        text(p, tag, str(i + 1), 12, WHITE, QtGui.QFont.Weight.Black, True)
        if not filled:
            gc = QPointF(cx, fy - min(110.0, slot_w * 0.55))
            pen = QtGui.QPen(col(BLUE, 110), 2); pen.setDashPattern([4, 4]); pen.setDashOffset(now * 8)
            p.setPen(pen); p.setBrush(col(WHITE, 120)); p.drawEllipse(gc, 26, 26)
            text(p, QRectF(gc.x() - 26, gc.y() - 27, 52, 52), "+", 30, col(BLUE, 190), QtGui.QFont.Weight.Black)
            text(p, QRectF(cx - slot_w / 2, fy + 37, slot_w, 22), "EMPTY", 11, col(GREY, 200),
                 QtGui.QFont.Weight.Black, True, AF.AlignCenter, 3)

    def _stage_model(self, p, e, cx, fy, slot_w, h, S, now, rotating):
        bb = e.bb()
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
        bcx = (bb[0] + bb[2]) / 2
        k = h / max(bh, 1.0)          # one height for the whole formation - that is the point
        a = clamp((now - e.sel_t) / 0.5, 0.0, 1.0)
        pop = ease_back(a)
        p.setPen(NOPEN); p.setBrush(col(NAVY_D, 60))
        p.drawEllipse(QPointF(cx, fy), max(bw * k * 0.3, 18), max(bw * k * 0.06, 5))
        if a < 1.0:                                     # summon beam + ring
            bwid = slot_w * 0.55 * (1.0 - a * 0.6)
            beam = QRectF(cx - bwid / 2, S.top(), bwid, fy - S.top())
            bg = QtGui.QLinearGradient(beam.topLeft(), beam.topRight())
            bg.setColorAt(0, col(CYAN, 0)); bg.setColorAt(0.5, col(WHITE, 230 * (1 - a))); bg.setColorAt(1, col(CYAN, 0))
            p.fillRect(beam, bg)
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QtGui.QPen(col(CYAN, 255 * (1 - a)), 3))
            rr = min(slot_w * 0.38, 80.0) * (1 + a * 1.4)
            p.drawEllipse(QPointF(cx, fy), rr, rr * 0.3)
        p.save()
        p.translate(cx, fy); p.scale(0.9 + 0.1 * pop, max(0.02, pop)); p.translate(-cx, -fy)
        p.setOpacity(clamp(a * 2.5, 0.0, 1.0))
        if e.img is not None:
            p.drawImage(QRectF(cx - bcx * k, fy - bb[3] * k, IW * k, IH * k), e.img)
        elif e.failed:
            self._silhouette(p, cx, fy, bh * k)
        else:
            spinner(p, QPointF(cx, fy - bh * k * 0.5), 16, now)
        p.restore()
        hit = QRectF(cx - max(bw * k, 40) / 2, fy - bh * k, max(bw * k, 40), bh * k)
        self.hits.append((hit, ("stage", e)))
        if self.hover == ("stage", e) and rotating is None:
            bc = QPointF(min(hit.right(), cx + slot_w * 0.42) - 6, hit.top() + 16)
            p.setPen(QtGui.QPen(WHITE, 2)); p.setBrush(RED); p.drawEllipse(bc, 13, 13)
            p.setPen(QtGui.QPen(WHITE, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(bc.x() - 5, bc.y() - 5), QPointF(bc.x() + 5, bc.y() + 5))
            p.drawLine(QPointF(bc.x() + 5, bc.y() - 5), QPointF(bc.x() - 5, bc.y() + 5))

    @staticmethod
    def _portrait_src(e, port):
        """Which part of her baked still fills this card's portrait frame.

        bake already cut her portrait window, so all this has to do is match its aspect to
        the card's: fill the frame and crop the overflow, always from the top and the middle,
        so that even a very short card cannot cut her head off.
        """
        sw, sh = e.still.width(), e.still.height()
        pa = port.width() / max(port.height(), 1.0)
        if sw <= sh * pa:                                  # too narrow: keep her full width
            return QRectF(0.0, 0.0, sw, sw / pa)
        w = sh * pa
        return QRectF((sw - w) / 2.0, 0.0, w, sh)

    def _silhouette(self, p, cx, fy, h, label=True):
        p.setPen(NOPEN); p.setBrush(col(NAVY, 60))
        p.drawEllipse(QPointF(cx, fy - h * 0.86), h * 0.09, h * 0.09)
        p.drawRoundedRect(QRectF(cx - h * 0.12, fy - h * 0.75, h * 0.24, h * 0.75), h * 0.08, h * 0.08)
        if label:
            text(p, QRectF(cx - 60, fy - h * 0.5, 120, 20), "NO PREVIEW", 11, WHITE, QtGui.QFont.Weight.Black, True)

    def _name_plate(self, p, e, cx, fy, slot_w, i):
        w = min(slot_w * 0.86, 188.0)
        plate = QRectF(cx - w / 2, fy + 37, w, 24)
        p.setPen(NOPEN); p.setBrush(NAVY); p.drawPolygon(para(plate, 9))
        p.setBrush(YELLOW); p.drawPolygon(para(QRectF(plate.left() + 2, plate.top(), 9, plate.height()), 6))
        text(p, plate.adjusted(18, 0, -10, 0), e.name, 13, WHITE, QtGui.QFont.Weight.Black, True,
             AF.AlignCenter, 0.3, elide=True)
        if e.slot.isVisible():
            d = QRectF(plate.right() - 14, plate.top() - 6, 12, 12)
            p.setBrush(GREEN); p.setPen(QtGui.QPen(WHITE, 2)); p.drawEllipse(d)

    # ---- roster ----
    def _paint_roster_head(self, p, H, now):
        p.fillRect(H, col(WHITE, 215))
        p.fillRect(QRectF(H.left(), H.top(), H.width(), 1.5), col(BLUE, 60))
        x = H.left() + 22
        p.setPen(NOPEN); p.setBrush(BLUE); p.drawPolygon(para(QRectF(x, H.top() + 11, 7, H.height() - 22), 3))
        text(p, QRectF(x + 16, H.top(), 140, H.height()), "STUDENTS", 16, NAVY, QtGui.QFont.Weight.Black, True,
             AF.AlignLeft | AF.AlignVCenter, 1.5)
        x += 136
        n_on = sum(1 for e in self.entries if e.slot.isVisible())
        tabs = (("all", f"All  {len(self.entries)}"), ("selected", f"Selected  {len(self.team)}"),
                ("desktop", f"On Desktop  {n_on}"))
        for key, label in tabs:
            r = QRectF(x, H.top() + 8, 128, H.height() - 16)
            active = self.filter == key; hov = self.hover == ("tab", key)
            path = poly_path(para(r, 8))
            if active:
                p.setPen(NOPEN); p.setBrush(BLUE)
            else:
                p.setPen(QtGui.QPen(col(BLUE, 90), 1)); p.setBrush(col(SKY, 230) if hov else col(WHITE, 250))
            p.drawPath(path)
            text(p, r, label, 12, WHITE if active else NAVY, QtGui.QFont.Weight.Black, True)
            self.hits.append((path, ("tab", key)))
            x += 136
        text(p, QRectF(x + 10, H.top(), H.right() - x - 32, H.height()),
             "click: add / remove  \u00b7  wheel: scroll  \u00b7  Enter: deploy  \u00b7  Esc: close", 11, GREY,
             QtGui.QFont.Weight.Bold, False, AF.AlignRight | AF.AlignVCenter, elide=True)

    def _paint_roster(self, p, Rr, now):
        lst = self._filtered()
        pad, gap = 28.0, 16.0
        card_h = max(80.0, Rr.height() - 30)
        card_w = card_h * 0.76
        self.card_step = card_w + gap
        content = pad * 2 + len(lst) * (card_w + gap) - (gap if lst else 0)
        self.max_scroll = max(0.0, content - Rr.width())
        self.scroll_target = clamp(self.scroll_target, 0.0, self.max_scroll)
        self.scroll = clamp(self.scroll, 0.0, self.max_scroll)
        first = max(0, int((self.scroll - pad) // self.card_step))
        end = min(len(lst), first + int(Rr.width() / self.card_step) + 3)
        self._visible_cards = lst[first:end]
        p.save(); p.setClipRect(Rr, Qt.ClipOperation.IntersectClip)
        for idx in range(first, end):
            e = lst[idx]
            x = Rr.left() + pad - self.scroll + idx * self.card_step
            r = QRectF(x, Rr.top() + 12, card_w, card_h)
            self._card(p, e, r, now, idx)
        if not lst:
            msg = {"selected": "No students in the formation yet", "desktop": "Nobody is on the desktop"}.get(
                self.filter, "No models found in model/")
            text(p, Rr, msg, 15, col(GREY, 230), QtGui.QFont.Weight.Black, True)
        # edge fades + arrows
        for side in (-1, 1):
            can = self.scroll > 1 if side < 0 else self.scroll < self.max_scroll - 1
            if not can: continue
            w = 60.0
            er = QRectF(Rr.left() if side < 0 else Rr.right() - w, Rr.top(), w, Rr.height() - 14)
            fg = QtGui.QLinearGradient(er.topLeft(), er.topRight())
            a0, a1 = (col(QtGui.QColor(228, 243, 255), 235), col(QtGui.QColor(228, 243, 255), 0))
            fg.setColorAt(0, a0 if side < 0 else a1); fg.setColorAt(1, a1 if side < 0 else a0)
            p.fillRect(er, fg)
            bc = QPointF(er.left() + 22 if side < 0 else er.right() - 22, er.center().y())
            hov = self.hover == ("arrow", side)
            p.setPen(QtGui.QPen(col(BLUE, 120), 1)); p.setBrush(BLUE if hov else col(WHITE, 245))
            p.drawEllipse(bc, 17, 17)
            p.setPen(QtGui.QPen(WHITE if hov else BLUE, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            p.drawPolyline(QtGui.QPolygonF([QPointF(bc.x() - 3 * side, bc.y() - 7), QPointF(bc.x() + 4 * side, bc.y()),
                                            QPointF(bc.x() - 3 * side, bc.y() + 7)]))
            self.hits.append((QRectF(bc.x() - 17, bc.y() - 17, 34, 34), ("arrow", side)))
        if self.max_scroll > 0:
            track = QRectF(Rr.left() + pad, Rr.bottom() - 9, Rr.width() - pad * 2, 4)
            p.setPen(NOPEN); p.setBrush(col(BLUE, 35)); p.drawRoundedRect(track, 2, 2)
            frac = Rr.width() / max(content, 1.0)
            tw = max(40.0, track.width() * frac)
            tx = track.left() + (track.width() - tw) * (self.scroll / self.max_scroll)
            p.setBrush(BLUE); p.drawRoundedRect(QRectF(tx, track.top(), tw, 4), 2, 2)
        p.restore()

    def _card(self, p, e, r, now, idx):
        sel = e in self.team
        order = self.team.index(e) + 1 if sel else 0
        on = e.slot.isVisible()
        a = ease_out((now - self.t_open - 0.1 - idx * 0.045) / 0.4)
        hit = QRectF(r)
        r = r.translated(0, (1 - a) * 40 - 8 * e.hover)
        p.save()
        p.setOpacity(a)
        c = r.center(); p.translate(c); p.shear(-0.06, 0); p.translate(-c)
        p.setPen(NOPEN); p.setBrush(col(NAVY_D, 35 + 35 * e.hover)); p.drawRoundedRect(r.translated(0, 6), 10, 10)
        path = rounded(r, 10)
        bg = QtGui.QLinearGradient(r.topLeft(), r.bottomLeft())
        bg.setColorAt(0, QtGui.QColor.fromHsv(e.hue, 80, 255)); bg.setColorAt(0.65, QtGui.QColor.fromHsv(e.hue, 28, 255))
        bg.setColorAt(1, WHITE)
        p.setBrush(bg); p.drawPath(path)
        p.save(); p.setClipPath(path, Qt.ClipOperation.IntersectClip)
        p.setPen(QtGui.QPen(col(WHITE, 80), 7))
        for k in range(-6, 14):
            sx = r.left() + k * 24
            p.drawLine(QPointF(sx, r.bottom()), QPointF(sx + r.height() * 0.6, r.top()))
        # No ring behind her head: the portrait is a cut-out on the card's own gradient, and
        # anything drawn under it shows through as a halo around her.
        port = QRectF(r.left(), r.top(), r.width(), r.height() - 48)
        if e.still is not None:
            p.drawImage(port, e.still, self._portrait_src(e, port))
        elif e.still_state:                                   # baked and empty: she drew nothing
            self._silhouette(p, port.center().x(), port.bottom(), port.height() * 1.05, label=False)
        else:
            spinner(p, port.center(), 15, now)
        plate = QRectF(r.left(), r.bottom() - 48, r.width(), 48)
        pg = QtGui.QLinearGradient(plate.topLeft(), plate.bottomLeft())
        pg.setColorAt(0, col(WHITE, 238)); pg.setColorAt(1, WHITE)
        p.fillRect(plate, pg)
        p.fillRect(QRectF(plate.left(), plate.top(), plate.width(), 2), col(BLUE, 70) if not sel else BLUE)
        text(p, plate.adjusted(12, 4, -10, -21), e.name, 15, NAVY, QtGui.QFont.Weight.Black, True,
             AF.AlignLeft | AF.AlignVCenter, elide=True)
        text(p, plate.adjusted(12, 26, -10, -5), e.file, 10, GREY, QtGui.QFont.Weight.DemiBold, False,
             AF.AlignLeft | AF.AlignVCenter, elide=True)
        if sel:
            p.fillRect(r, col(BLUE, 22))
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        if sel:
            p.setPen(QtGui.QPen(col(BLUE, 80 + 40 * math.sin(now * 4)), 8)); p.drawPath(path)
            p.setPen(QtGui.QPen(BLUE, 3)); p.drawPath(path)
        elif e.hover > 0.05:
            p.setPen(QtGui.QPen(col(BLUE, 200 * e.hover), 2.5)); p.drawPath(path)
        else:
            p.setPen(QtGui.QPen(col(WHITE, 230), 2)); p.drawPath(path)
            p.setPen(QtGui.QPen(col(NAVY, 35), 1)); p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 11, 11)
        if sel:
            bc = QPointF(r.right() - 17, r.top() + 17)
            p.setPen(QtGui.QPen(WHITE, 2.5)); p.setBrush(BLUE); p.drawEllipse(bc, 15, 15)
            text(p, QRectF(bc.x() - 15, bc.y() - 15, 30, 30), str(order), 14, WHITE, QtGui.QFont.Weight.Black, True)
        tag = None
        if e.still is None and e.still_state: tag = ("NO PREVIEW", col(GREY), WHITE)
        elif on and sel: tag = ("ON DESKTOP", GREEN, WHITE)
        elif on and not sel: tag = ("RECALL", ORANGE, WHITE)
        elif sel and not on: tag = ("NEW", YELLOW, NAVY_D)
        if tag:
            label, bgc, fgc = tag
            tw = QtGui.QFontMetrics(font(10, QtGui.QFont.Weight.Black, True, 1.0)).horizontalAdvance(label) + 18
            tr = QRectF(r.left() + 8, r.top() + 9, tw, 19)
            p.setPen(NOPEN); p.setBrush(bgc); p.drawPolygon(para(tr, 5))
            text(p, tr, label, 10, fgc, QtGui.QFont.Weight.Black, True, AF.AlignCenter, 1.0)
        p.restore()
        self.hits.append((hit, ("card", e)))

    # ---- bottom bar ----
    def _button(self, p, r, label, key, slant=10, accent=BLUE, px=14):
        hov = self.hover == key
        path = poly_path(para(r, slant))
        p.setPen(QtGui.QPen(col(accent, 160 if hov else 90), 1.4))
        p.setBrush(col(SKY, 255) if hov else col(WHITE, 252))
        p.drawPath(path)
        p.setPen(NOPEN); p.setBrush(accent)
        p.drawPolygon(para(QRectF(r.left() + 3, r.top() + 4, 6, r.height() - 8), 3))
        text(p, r.adjusted(slant, 0, -4, 0), label, px, NAVY, QtGui.QFont.Weight.Black, True)
        self.hits.append((path, key))

    def _paint_bottom(self, p, B, now):
        g = QtGui.QLinearGradient(B.topLeft(), B.bottomLeft())
        g.setColorAt(0, col(WHITE, 236)); g.setColorAt(1, col(WHITE, 252))
        p.fillRect(B, g)
        p.fillRect(QRectF(B.left(), B.top(), B.width(), 1.5), col(BLUE, 60))
        cy = B.center().y()
        # startup checkbox
        on = bool(self.app.cfg.get("picker_on_start", True))
        box = QRectF(B.left() + 26, cy - 11, 22, 22)
        hov = self.hover == ("startup",)
        p.setPen(QtGui.QPen(BLUE, 2 if hov else 1.5)); p.setBrush(BLUE if on else col(WHITE, 255))
        p.drawRoundedRect(box, 5, 5)
        if on:
            p.setPen(QtGui.QPen(WHITE, 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPolyline(QtGui.QPolygonF([QPointF(box.left() + 5, cy), QPointF(box.left() + 9.5, cy + 5),
                                            QPointF(box.right() - 5, cy - 5)]))
        text(p, QRectF(box.right() + 10, B.top(), 210, B.height()), "Open this screen at startup", 12, NAVY,
             QtGui.QFont.Weight.Bold, False, AF.AlignLeft | AF.AlignVCenter)
        self.hits.append((QRectF(box.left() - 4, cy - 16, 250, 32), ("startup",)))
        pb = QRectF(box.left() + 262, cy - 21, 176, 42)
        self._button(p, pb, "\u2699  Control Panel", ("panel",), px=13)
        # right side
        dep = QRectF(B.right() - 26 - 250, cy - 28, 250, 56)
        allb = QRectF(dep.left() - 14 - 120, cy - 21, 120, 42)
        clr = QRectF(allb.left() - 10 - 110, cy - 21, 110, 42)
        self._button(p, clr, "Clear", ("clear",), accent=ORANGE)
        self._button(p, allb, "Select All", ("all",))
        adds, rec = self.diff()
        verb, count, live = self.deploy_state()
        # summary
        sr = QRectF(pb.right() + 16, B.top(), clr.left() - pb.right() - 30, B.height())
        if sr.width() > 80:
            if self._pending:
                text(p, sr, f"Deploying {count} student{'s' if count != 1 else ''}\u2026", 13, BLUE_D,
                     QtGui.QFont.Weight.Black, True, AF.AlignRight | AF.AlignVCenter, elide=True)
            elif adds or rec:
                parts = []
                if adds: parts.append(f"{adds} to deploy")
                if rec: parts.append(f"{rec} to recall")
                text(p, sr, "   \u00b7   ".join(parts), 13, BLUE_D, QtGui.QFont.Weight.Black, True,
                     AF.AlignRight | AF.AlignVCenter, elide=True)
            else:
                text(p, sr, "Formation matches the desktop", 12, GREY, QtGui.QFont.Weight.Bold, True,
                     AF.AlignRight | AF.AlignVCenter, elide=True)
        # DEPLOY / RECALL - greyed out and deaf to the mouse when a click would do nothing
        hov = live and self.hover == ("deploy",)
        path = poly_path(para(dep, 16))
        if live:
            if hov:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QtGui.QPen(col(YELLOW_D, 150), 9)); p.drawPath(path)
            else:
                pulse = 0.5 + 0.5 * math.sin(now * 4.2)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QtGui.QPen(col(YELLOW_D, 60 + 90 * pulse), 9)); p.drawPath(path)
            dg = QtGui.QLinearGradient(dep.topLeft(), dep.bottomLeft())
            dg.setColorAt(0, YELLOW_L.lighter(106) if hov else YELLOW_L)
            dg.setColorAt(0.5, YELLOW.lighter(105) if hov else YELLOW)
            dg.setColorAt(1, YELLOW_D)
            p.setPen(QtGui.QPen(QtGui.QColor(206, 146, 0), 1.5)); p.setBrush(dg); p.drawPath(path)
        else:
            dg = QtGui.QLinearGradient(dep.topLeft(), dep.bottomLeft())
            dg.setColorAt(0, QtGui.QColor(246, 248, 251)); dg.setColorAt(1, QtGui.QColor(222, 230, 240))
            p.setPen(QtGui.QPen(QtGui.QColor(196, 208, 223), 1.5)); p.setBrush(dg); p.drawPath(path)
        p.save(); p.setClipPath(path, Qt.ClipOperation.IntersectClip)
        if live:
            p.fillRect(QRectF(dep.left(), dep.top(), dep.width(), dep.height() * 0.45), col(WHITE, 70))
            p.setPen(QtGui.QPen(col(WHITE, 55), 10))
            sx = dep.left() + ((now * 90) % (dep.width() + 120)) - 60
            p.drawLine(QPointF(sx, dep.bottom()), QPointF(sx + 30, dep.top()))
        p.restore()
        ink = NAVY_D if live else GREY                       # greyed ink when it is off
        n = len(self.team)
        label = verb if not live else f"{verb} {count} student{'s' if count != 1 else ''}"
        # "DEPLOYING" stays big; a counted headline has to give up size to fit the button
        big = len(label)
        px = 25 if big <= 7 else 21 if big <= 13 else 17
        text(p, QRectF(dep.left() + 12, dep.top() + 3, dep.width() - 24, dep.height() * 0.62), label, px,
             ink, QtGui.QFont.Weight.Black, True, AF.AlignCenter, 2.0 if big <= 13 else 0.5, elide=True)
        # the same sub-line in every state, greyed with the button when it is off: how many
        # students the formation has, whatever the headline is saying about the change.
        sub = f"\u51fa\u6483  \u00b7  {n} student{'s' if n != 1 else ''}"
        text(p, QRectF(dep.left() + 10, dep.top() + dep.height() * 0.58, dep.width() - 26, dep.height() * 0.36),
             sub, 11, col(ink, 200 if live else 150), QtGui.QFont.Weight.Bold, False, elide=True)
        if live:                                              # no hit region: a dead button
            self.hits.append((path, ("deploy",)))            # simply does not take clicks

    # ------------------------------------------------------------------
    #  Input
    # ------------------------------------------------------------------
    def _hit(self, pos):
        for shape, key in reversed(self.hits):
            if shape.contains(pos): return key
        return None

    def _set_hover(self, key, force=False):
        if force or key != self.hover:
            self.hover = key
            clickable = key is not None and key[0] != "drag"
            self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self.drag_origin is not None:
            self.move(ev.globalPosition().toPoint() - self.drag_origin); return
        if self.rot_drag is not None:
            e, x0, t0, moved = self.rot_drag
            dx = pos.x() - x0
            if abs(dx) > 4: moved = True
            if moved: e.turn = t0 + dx * 0.012
            self.rot_drag = (e, x0, t0, moved)
            self.setCursor(Qt.CursorShape.SizeHorCursor if moved else Qt.CursorShape.PointingHandCursor)
            return
        self._set_hover(self._hit(pos))

    def leaveEvent(self, ev):
        if self.rot_drag is None and self.drag_origin is None: self._set_hover(None)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(ev)
        pos = ev.position()
        key = self._hit(pos)
        self.press = key
        if key is not None and key[0] == "drag":
            self.drag_origin = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif key is not None and key[0] == "stage":
            self.rot_drag = (key[1], pos.x(), key[1].turn, False)

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(ev)
        pos = ev.position()
        key = self._hit(pos)
        if self.drag_origin is not None:
            self.drag_origin = None; self.press = None; return
        if self.rot_drag is not None:
            e, _x0, _t0, moved = self.rot_drag
            self.rot_drag = None
            if not moved and key == ("stage", e): self.toggle(e)
            self.press = None
            self._set_hover(self._hit(pos), force=True)
            return
        if key is not None and key == self.press:
            self._click(key)
        self.press = None

    def mouseDoubleClickEvent(self, ev):
        key = self._hit(ev.position())
        if key is not None and key[0] == "drag":         # double-click the title bar: re-centre
            self._place(); return
        # Qt sends press / release / double-click / release: treat the 2nd click as a click
        self.mousePressEvent(ev)

    def _click(self, key):
        kind = key[0]
        if kind == "back" and getattr(self.app, "arena", None) is not None:
            # the back arrow returns to the title screen (Desktop Pets / Battle Arena)
            self.close_animated()
            QTimer.singleShot(200, self.app.show_title)
        elif kind in ("close", "back"): self.close_animated()
        elif kind == "card": self.toggle(key[1])
        elif kind == "tab":
            self.filter = key[1]; self.scroll_target = 0.0
        elif kind == "arrow": self.scroll_target += key[1] * self.card_step * 3
        elif kind == "stage_arrow":
            self.stage_scroll_target = clamp(self.stage_scroll_target + key[1] * self.stage_unit * 3,
                                             0.0, self.stage_max_scroll)
        elif kind == "deploy": self.deploy()
        elif kind == "clear": self.clear()
        elif kind == "all": self.select_all()
        elif kind == "panel":
            self.close_animated()
            QTimer.singleShot(200, self.app.show_panel)
        elif kind == "startup":
            self.app.set("picker_on_start", not bool(self.app.cfg.get("picker_on_start", True)))
        self.update()

    def wheelEvent(self, ev):
        """The wheel scrolls whichever half the cursor is over: the formation on the stage,
        the roster of cards below it. One wheel, two rows, no modifier to remember."""
        d = ev.angleDelta().y() or ev.angleDelta().x()
        if getattr(self, "stage_R", None) is not None and self.stage_R.contains(ev.position()):
            self.stage_scroll_target = clamp(self.stage_scroll_target - d * 0.9, 0.0, self.stage_max_scroll)
        else:
            self.scroll_target = clamp(self.scroll_target - d * 0.9, 0.0, self.max_scroll)
        ev.accept()

    def keyPressEvent(self, ev):
        k = ev.key(); mods = ev.modifiers()
        if k == Qt.Key.Key_Escape: self.close_animated()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter): self.deploy()
        elif k == Qt.Key.Key_A and (mods & Qt.KeyboardModifier.ControlModifier): self.select_all()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace): self.clear()
        elif k == Qt.Key.Key_Left: self.scroll_target -= self.card_step
        elif k == Qt.Key.Key_Right: self.scroll_target += self.card_step
        elif k == Qt.Key.Key_Home: self.scroll_target = 0.0
        elif k == Qt.Key.Key_End: self.scroll_target = self.max_scroll
        else: return super().keyPressEvent(ev)


# =============================================================================
#  "Now Loading" card shown while the models are read
# =============================================================================
class LoadingSplash(QtWidgets.QWidget):
    def __init__(self, names):
        flags = (Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.names = list(names)
        self.resize(520, 190)
        self.qapp = None
        self.target = 0.0                # 0..1 where the loader says it is
        self.shown = 0.0                 # 0..1 what the bar draws (eases toward target)
        self.status = "Starting…"
        self._drag_off = None            # cursor offset inside the card while it is being dragged

    # -- drag the card by any point on it, like the other windows -------------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_off = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag_off is not None and (ev.buttons() & Qt.MouseButton.LeftButton):
            self.move(ev.globalPosition().toPoint() - self._drag_off)
            ev.accept()
        else:
            super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._drag_off is not None:
            self._drag_off = None
            self.unsetCursor()
            ev.accept()
        else:
            super().mouseReleaseEvent(ev)

    def show_now(self, qapp):
        self.qapp = qapp
        scr = qapp.screenAt(QtGui.QCursor.pos()) or qapp.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)
        self.show(); self.raise_()
        for _ in range(3):
            qapp.processEvents()
        self.repaint()
        qapp.processEvents()

    def set_progress(self, frac, status=None):
        """Move the bar to `frac` (0..1) and say what is being done.

        The models load on the main thread, so nothing else can repaint the card
        while they are read. Every call therefore pumps the event loop itself:
        it eases the bar from where it was to the new value over a few frames,
        so the fill visibly runs instead of jumping from 0 to done.
        """
        self.target = clamp(float(frac), 0.0, 1.0)
        if status is not None:
            self.status = status
        if self.qapp is None:
            self.shown = self.target
            return
        start, end = self.shown, self.target
        if end <= start:                       # never run the bar backwards
            self.target = self.shown = max(start, end)
            self.repaint(); self.qapp.processEvents()
            return
        steps = 8 if (end - start) > 0.02 else 1
        for i in range(1, steps + 1):
            self.shown = start + (end - start) * ease_out(i / steps)
            self.repaint()                     # synchronous paint, not a queued update
            self.qapp.processEvents()
            if steps > 1:
                time.sleep(0.012)

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        R = QRectF(self.rect()).adjusted(14, 14, -14, -14)
        p.setPen(NOPEN)
        for i in range(14, 0, -2):
            p.setBrush(col(NAVY_D, 3 + (14 - i)))
            p.drawRoundedRect(R.adjusted(-i, -i + 3, i, i + 3), 16 + i, 16 + i)
        path = rounded(R, 14)
        g = QtGui.QLinearGradient(R.topLeft(), R.bottomRight())
        g.setColorAt(0, QtGui.QColor(248, 252, 255)); g.setColorAt(1, QtGui.QColor(222, 240, 255))
        p.setBrush(g); p.drawPath(path)
        p.save(); p.setClipPath(path)
        p.setPen(QtGui.QPen(col(BLUE, 16), 1))
        x = R.left() - R.height()
        while x < R.right():
            p.drawLine(QPointF(x, R.bottom()), QPointF(x + R.height(), R.top())); x += 20
        p.setPen(QtGui.QPen(col(BLUE, 40), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(R.right() - 60, R.center().y()), 70, 70)
        p.setPen(QtGui.QPen(col(PINK, 120), 3)); p.drawEllipse(QPointF(R.right() - 60, R.center().y()), 50, 16)
        p.restore()
        p.setPen(NOPEN); p.setBrush(BLUE); p.drawPolygon(para(QRectF(R.left() + 22, R.top() + 26, 8, 40), 4))
        text(p, QRectF(R.left() + 40, R.top() + 18, 400, 34), "Now Loading\u2026", 28, NAVY,
             QtGui.QFont.Weight.Black, True, AF.AlignLeft | AF.AlignVCenter)
        text(p, QRectF(R.left() + 42, R.top() + 52, 400, 18), "SCHALE  \u00b7  STUDENT RECORDS", 10, BLUE_D,
             QtGui.QFont.Weight.Bold, False, AF.AlignLeft | AF.AlignVCenter, 2.5)
        n = len(self.names)
        who = ", ".join(self.names[:5]) + (f" +{n - 5}" if n > 5 else "")
        text(p, QRectF(R.left() + 42, R.top() + 82, R.width() - 160, 20),
             f"Preparing {n} student{'s' if n != 1 else ''}: {who}", 12, GREY, QtGui.QFont.Weight.Bold, False,
             AF.AlignLeft | AF.AlignVCenter, elide=True)
        text(p, QRectF(R.left() + 42, R.top() + 102, R.width() - 160, 18), self.status, 11, BLUE_D,
             QtGui.QFont.Weight.Bold, False, AF.AlignLeft | AF.AlignVCenter, elide=True)
        frac = clamp(self.shown, 0.0, 1.0)
        bar = QRectF(R.left() + 42, R.bottom() - 34, R.width() - 170, 8)
        p.setBrush(col(BLUE, 40)); p.drawRoundedRect(bar, 4, 4)
        if frac > 0.0:
            fill = QRectF(bar.left(), bar.top(), max(bar.height(), bar.width() * frac), bar.height())
            p.setBrush(BLUE); p.drawRoundedRect(fill, 4, 4)
            p.setBrush(col(WHITE, 90))          # soft highlight so the fill reads as a bar, not a line
            p.drawRoundedRect(QRectF(fill.left() + 2, fill.top() + 1, max(0.0, fill.width() - 4), 2.5), 1.2, 1.2)
        text(p, QRectF(bar.right() + 10, bar.top() - 9, 56, 26), f"{int(round(frac * 100))}%", 14, NAVY,
             QtGui.QFont.Weight.Black, True, AF.AlignLeft | AF.AlignVCenter)
        p.end()
