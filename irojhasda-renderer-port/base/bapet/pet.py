"""Pet: the transparent window for one character. Behaviour lives in the pet_* mixins."""
from __future__ import annotations

import math
import random
import time
import traceback
from collections import deque

import numpy as np
from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QPoint, Qt

from dialogue import Conversation, ConversationField
from .combat_config import MAG_SIZE, NERVE_RANGE
from .config import BASE_H, BASE_W
from .mathutil import clamp
from .pet_animdbg import PetAnimDebug
from .pet_attack import PetAttack
from .pet_behavior import PetBehavior
from .pet_combat import PetCombat
from .pet_cover import PetCover
from .pet_heading import PetHeading
from .pet_input import PetInput
from .pet_rag3d import PetRag3D
from .pet_rag_pose import PetRagPose
from .pet_rag_step import PetRagStep
from .pet_ragdoll import PetRagdoll
from .pet_render import PetRender
from .pet_skeleton import PetRagSkeleton
from .pet_speech import PetSpeech
from .pet_state import PetState
from .pet_window import PetWindow
from .ragdoll_config import RAG_COM_FRAC
from .renderer import Renderer
from .rig import Rig


class Pet(
        PetRagSkeleton, PetHeading, PetWindow, PetSpeech, PetState, PetAttack, PetCombat,
        PetCover, PetRagdoll, PetRagStep, PetRagPose, PetRag3D, PetBehavior, PetInput, PetRender,
        PetAnimDebug, QtWidgets.QWidget
):
    PADB = Renderer.PADB
    LOOPS = ("idle", "walk", "alert")

    # The conversation state itself lives on self.conv (see dialogue/conversation.py).
    # These keep the old flat attribute names working for everything that still
    # reads them: the panel status line, the menus, the rest of this class.
    chat_lines = ConversationField("lines")
    chat_with = ConversationField("other")
    chat_i = ConversationField("index")
    chat_next = ConversationField("next_t")
    chat_lead = ConversationField("lead")
    chat_topic = ConversationField("topic")
    chat_until = ConversationField("until")
    meet_target = ConversationField("meet_target")
    avoid = ConversationField("avoid")
    last_topic = ConversationField("last_topic")

    def __init__(self, A, ch):
        super().__init__()
        self.A, self.cfg = A, A.cfg
        self.ch = ch                                     # this character's data
        self.name = ch.name
        self.model = ch.model
        self.clips = ch.clips
        # Vehicle state, for a profile with `ride` clips (Iroha's tank). ride_on is
        # which clip set and window are live, ride_t is the vehicle's opacity and
        # ride_to where that is heading. None of it exists for an ordinary model.
        self.ride_on = False; self.ride_t = 0.0; self.ride_to = 0.0
        self.rig = Rig(ch.model)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.px = self.py = 0.0; self.vx = self.vy = 0.0
        self.W, self.H, self.dpr, self.G = 100, 100, 1.0, 2600.0
        self.grounded = False; self.support = None; self.bounces = 0
        self.state = "idle"; self.state_t = 0.0; self.state_dur = 0.0; self.shot_key = None
        self.anim_key = "idle"; self.air_kind = "jump"
        self.atk = None; self._muzzle_node = False       # shooting event state (see try_shoot)
        # ammo (combat_index.json): the magazine belongs to HER, not to one fight, so what she spent in the
        # last fight is still gone in the next until she reloads (combat_over / the reload phase)
        self.ammo_max = ch.ammo_to_reload or random.randint(*MAG_SIZE)
        self.shots_per_attack = max(1, min(ch.ammo_per_attack, self.ammo_max))
        self.ammo = self.ammo_max
        self.flee = None                                 # running-away state (see start_flee)
        self.stress = 0.0; self.stress_t = time.monotonic()   # shakenness 0..1, see stress_now
        self.flinch_until = 0.0                          # monotonic time her aim recovers from a hit
        self.no_flee_until = 0.0                         # monotonic time before which she won't break and run
        self.nerve = random.uniform(*NERVE_RANGE)        # how much fire she can take before she breaks
        self.walk_dir = 1; self.edge_decided = False; self.step_off = False
        self.depth = 0.0                         # -1 far .. +1 near; shown as size (see depth_scale)
        self._yo_seen = 0.0
        # Per-character hand rotation (Ctrl+wheel / rotate-drag). Degrees, ON TOP of the
        # global panel rotation. Eases back to 0 (facing the desktop) once released.
        self.yaw_user = self.pitch_user = self.tilt_user = 0.0
        self.rot_touch = 0.0
        # autonomous "look around": body turns left / right and comes back (radians)
        self.scan = None; self.scan_yaw = 0.0
        self.plan_t = 2.0; self.chat_t = random.uniform(25, 60)
        self.dragging = False; self.press_g = None; self.press_win = QPoint(); self.rotating = False
        self.rot_yaw0 = 0.0; self.hist = deque(maxlen=12); self.drag_started_say = 0.0
        self.gx = self.gy = 0.0; self.body_yaw = 0.0; self.head_yaw = self.head_pitch = 0.0
        self.roll = self.roll_v = 0.0; self.sq = self.sq_v = 0.0; self.zoom = 1.0
        self.ori_p = self.ori_t = 0.0          # eased pitch/tilt offsets (rad), see render_frame
        self.rot_pitch0 = self.rot_tilt0 = 0.0; self.rot_shift = False
        self.spin_t = -1.0; self.bow = 0.0; self.last_px = 0.0; self.last_py = 0.0
        self.warp_t = -1.0                 # teleport-in effect clock (pet_state.teleport_in), -1 = off
        # hold-physics drive: smoothed window velocity + 0..1 hold energy
        self.hold_vx = self.hold_vy = 0.0; self.hold_e = 0.0
        self.wind_vx = self.wind_vy = 0.0
        self.pitch_hold = self.pitch_hold_v = 0.0
        self.light = np.array([-0.5, 0.6, 0.8])
        self.head_px = (BASE_W / 2, BASE_H * 0.3)
        self.bubble = None; self.parts = []
        self.say_next = []; self.say_next_at = None     # later parts of a multi-part line, see say()
        self.intro_line = None                          # her spawn line, held until she is on screen
        self.recall_say = False                         # her summon line is up: landing must not talk over it
        self.emo = None                        # (kind, t0, dur): the emote balloon over her head (see vfx.emote)
        self.emo_seen = None                   # last emote drawn, so the mask refreshes when it ends
        # earliest time she may next swap her bubble for a reactive combat line, see say_combat
        self.say_lock = 0.0
        # --- pet-to-pet (all of it lives on self.conv) ---
        self.conv = Conversation(self, A.dialogue)
        self.gaze_peer = None; self.gaze_left = 0.0; self.gaze_cd = random.uniform(3.0, 9.0)
        # how much she cares about the cursor at all: re-rolled on a slow timer so a
        # distant cursor cannot make her head twitch every single frame
        self.cur_look = True; self.cur_roll_t = random.uniform(0.0, 1.5)
        # when she is ignoring the cursor she looks off at nothing in particular
        self.idle_pt = (0.0, 0.0); self.idle_t = random.uniform(0.0, 3.0)
        self.hover = False; self.hover_time = 0.0; self.hover_said = False
        self.pat_sign = 0; self.pat_events = []; self.pat_cool = 0.0; self.last_cur = QPoint()
        self.pat_run = 0.0; self.pat_leave = 0.0        # head-pat stroke tracking
        self.frame_n = 0; self.mask_dirty = True; self.mask_key = None
        self._pt = False; self.dt = 1 / 30.0
        self.img = None; self.alpha = None; self.raw = None
        self.bfont = QtGui.QFont(); self.bfont.setBold(True)
        # --- ragdoll (see the RAG_* block at the top of this file) ---
        self.ragdoll = False; self.rag_state = "fall"      # "fall" | "getup"
        self.rag_win = 1.0        # her window while limp, x her standing one (see PetWindow.rag_window)
        self.rag_t = 0.0; self.rag_rest = 0.0; self.rag_hit_t = 0.0
        self.rag_want_up = False          # "Get up" asked for while she was still flying
        self.rag_edge_slam = False        # this ragdoll came from hitting a screen edge
        self.rg_x = self.rg_y = 0.0; self.rg_vx = self.rg_vy = 0.0   # centre of mass, screen px
        self.rg_a = self.rg_w = 0.0            # body angle (0 = upright, + = head to screen-right), rad/s
        self.rg_ax = self.rg_ay = self.rg_alpha = 0.0               # smoothed accelerations for the limbs
        self.rg_prev_low = 0.0; self.rag_contact = False
        self.rg_top = None                            # surface she is resting on, if any
        self.rg_grav = 1.0; self.rag_strength = 1.0
        self.rg_a0 = self.rg_x0 = self.rg_y0 = 0.0                # get-up pose
        # articulated ragdoll (see ragphys.py and rag_spec)
        self.rag_owner = id(self) % 100000               # collision group / physics id
        self.rag_pm_failed = False                       # her rig cannot do pymunk; stop trying
        self.rag_phi0 = None                             # every bone's angle as she lay down
        self.rag_phi = None                              # ... scaled down during the blend
        self.rag_pm_bones = None                         # bones + parents, kept past detach
        self.rag_pel_dx = 0.0                             # real pelvis x minus window centre, px
        self.rag_drop = 0.0                              # pelvis -> feet, window px
        self.rag_com = RAG_COM_FRAC               # measured off her own pelvis, see rag_resolve
        self.rag_names = []                # lowercased bone names, see rag_bone
        self.rag_skel = None               # her RagSkeleton (or Rag3DSkeleton) while she is limp, else None
        # fully 3D ragdoll (see ragphys3d.py and pet_rag3d.py)
        self.rag3 = None                   # 3D state while limp or getting up: spawn matrix, post map, pose
        self.rag3_failed = False           # her rig cannot do the 3D engine; stop trying
        self.last_M = np.eye(4); self.rag_bones = []
        self.apply_flags(show=False)
        self.apply_scale()
        self.play("idle")
        self.rag_resolve()

    # ---------------- per-frame logic ----------------
    def tick(self, dt):
        self.frame_n += 1; self.dt = dt
        cur = QtGui.QCursor.pos(); now = time.time()
        self.cursor_logic(cur, dt, now)
        if self.ragdoll:
            try:
                if self.dragging and self.rag_skel is None:
                    self.rag_drag_step(dt)      # legacy rod: the cursor owns her
                else:
                    # With pymunk she is read from the physics world even while
                    # being dragged - the grab joint already moved the body, and
                    # her window follows the body in ragdoll_step_pm.
                    self.rag_pm_cursor(cur)
                    self.ragdoll_step(dt)
            except Exception:
                traceback.print_exc()
                self.rag_clear()               # also takes her out of the world
                self.reset_position()          # never leave her stuck mid-tumble
        elif self.dragging: pass
        elif not self.grounded: self.air(dt)
        else: self.behave(dt, cur)
        self.rig.advance(dt)
        self.advance_ride(dt)
        now = time.time()
        self.gaze_logic(dt, cur, now)
        self.chat_logic(dt, now)
        self.chat_t -= dt
        if self.chat_t <= 0:
            self.chat_t = random.uniform(30, 80)
            if self.grounded and not self.bubble and self.state == "idle" and not self.in_chat(now):
                self.say(self.line("idle"))
                if random.random() < 0.35: self.emote(random.choice(("note", "note", "dots", "idea")), 1.8)
        if self.bubble and now - self.bubble[1] > self.bubble[2]: self.bubble = None; self.mask_dirty = True
        self.pump_say(now)
        if self.intro_line is not None and self.isVisible() and self.grounded and not self.dragging:
            line, self.intro_line = self.intro_line, None
            if not self.bubble: self.say(line)
        if self.emo and now - self.emo[1] > self.emo[2]: self.emo = None; self.mask_dirty = True
        if self.spin_t >= 0:
            self.spin_t += dt
            if self.spin_t > 1.1: self.spin_t = -1.0
        if self.warp_t >= 0:
            self.warp_t += dt; self.mask_dirty = True
            if self.warp_t > self.WARP_DUR or self.ragdoll or self.dragging: self.warp_t = -1.0
        for q in self.parts:
            q["life"] += dt
            if q["life"] < 0: continue                     # hasn't been reached by the gust yet
            q["x"] += q["vx"] * dt; q["y"] += q["vy"] * dt
            if q["kind"] == "wind":
                k = math.exp(-1.4 * dt); q["vx"] *= k; q["vy"] *= k      # straight, just slows
            elif "g" in q:                                 # BA VFX particles: own gravity + drag (pet_speech)
                k = math.exp(-q["drag"] * dt); q["vx"] *= k; q["vy"] = q["vy"] * k + q["g"] * dt
            else:
                q["vx"] *= 0.985; q["vy"] = q["vy"] * 0.985 - (30 if q["kind"] != "puff" else -10) * dt
        self.parts = [q for q in self.parts if q["life"] < q["max"]]
        self.procedural(dt, cur)
        self.render_frame(cur)

    # ---------------- pet-to-pet per-frame logic ----------------
    def gaze_logic(self, dt, cur, now):
        """Decide who she looks at: the cursor (only when it is near), a friend,
        or nobody in particular. Never fights a cursor that is right next to her."""
        cfg = self.cfg
        hx, hy = self.px + self.head_px[0], self.py + self.head_px[1]
        d = math.hypot(cur.x() - hx, cur.y() - hy)
        # --- how interested is she in the cursor right now? ---
        # Close = almost certain she tracks it, far away = almost never. The roll is
        # re-made on a slow timer (not per frame) so a distant cursor cannot make her
        # head twitch, and the decision is sticky: once she looks away she keeps
        # glancing at a friend / into the room for a while.
        near = self.W * 0.9
        far = max(near + 1.0, self.W * max(0.5, float(cfg["cursor_gaze_range"])))
        t = clamp((d - near) / (far - near), 0.0, 1.0)          # 0 = on top of her, 1 = far away
        s = t * t * (3 - 2 * t)
        chance = float(cfg["cursor_gaze_far"]) + (1.0 - float(cfg["cursor_gaze_far"])) * (1.0 - s)
        self.cur_roll_t -= dt
        if self.cur_roll_t <= 0.0:
            self.cur_roll_t = random.uniform(1.0, 2.6)
            self.cur_look = (random.random() < chance) and not (self.dragging or self.state == "shot")
        cur_close = d < self.W * 1.2
        if cur_close or self.hover:
            self.cur_look = True                               # the user is right there: always
        if not cfg["look_at_cursor"]:
            self.cur_look = False
        # --- an idle gaze point, for when she is watching nobody in particular ---
        self.idle_t -= dt
        if self.idle_t <= 0.0:
            self.idle_t = random.uniform(2.5, 7.0)
            self.idle_pt = (random.uniform(-1.0, 1.0), random.uniform(-0.7, 0.35))
        if not cfg["peer_look"]:
            self.gaze_peer = None; self.gaze_left = 0.0; return
        # the user always wins: a nearby cursor overrides peer-gazing
        if self.dragging or self.state in ("shot", "shoot", "flee", "fall") or cur_close:
            self.gaze_peer = None; self.gaze_left = 0.0; self.gaze_cd = random.uniform(3.0, 9.0); return
        if self.gaze_left > 0:
            self.gaze_left -= dt
            if self.gaze_peer is not None and (not self.gaze_peer.isVisible()
                                               or abs(self.gaze_peer.feet_pos()[0] - self.feet_pos()[0]) > self.W * 4.0):
                self.gaze_peer = None
            if self.gaze_left <= 0: self.gaze_peer = None
            return
        self.gaze_cd -= dt
        if self.gaze_cd > 0 or not self.grounded or self.state not in ("idle", "alert", "walk"): return
        # a pet who is ignoring the cursor looks over at a friend more readily
        boost = 1.0 if self.cur_look else 2.2
        self.gaze_cd = random.uniform(4.0, 12.0) / max(0.05, cfg["peer_look_rate"] * boost)
        if random.random() > min(1.0, cfg["peer_look_rate"] * boost): return
        ps = [q for q in self.peers(max_dist=self.W * 2.4, same_surface=True) if q.grounded]
        if ps: self.gaze_peer = ps[0]; self.gaze_left = random.uniform(2.5, 6.0)
