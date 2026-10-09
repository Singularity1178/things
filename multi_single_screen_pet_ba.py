#!/usr/bin/env python3
"""
Multi Pet - Single Screen  -  Blue Archive 3D pets (one per .glb)
===================================================================
Same as multi_pet.py, but every character is locked to ONE screen: they
only ever spawn on it, walk on it, jump on windows that sit on it, and are
clamped if you try to drag them off the edge.
================================================================
Loads EVERY *.glb file in the model/ folder next to this script, creates
one independent character (own model, own rig, own animations, own dialogue)
for each of them, renders them with moderngl using a Blue-Archive-style
toon shader (cel ramp, tinted shadows, rim light, spec ticks, inverted-hull
outline), and shows them in transparent always-on-top PySide6 windows.

pip install PySide6 moderngl numpy "pymunk>=7,<8"

What lives where
  this file       App, Pet, Panel, Renderer - the pets themselves
  ragphys.py      the articulated ragdoll (pymunk). No Qt, no GL, so it can be
                    imported and tested on its own: `python test_ragphys.py`
  model/          the *.glb files, one character each
  dialogue/       everything they say, and who says it to whom
                    engine.py            look up one line for a character + event
                    conversation.py      pet-to-pet talking
                    system_watcher.py    computer-state tags (battery, cpu, ...)
                    data/*.json          the lines themselves
  Dialogue has no PySide import, so there is no import cycle and the lines can
  be edited as JSON without touching any code here.

Uses ONLY these animations per model:
  Cafe_Idle, Cafe_Walk, Cafe_Reaction, Formation_Idle, Formation_Pickup,
  Exs_Cutin, Tactical_Start

Interaction
  cursor            head/body look at it everywhere, light follows it
  together          they glance at each other and hold conversations of their
                    own (topics, reactions, greetings); a nearby cursor always
                    wins over looking at another character
  click             poke  (Cafe_Reaction)
  double-click      EX cut-in (Exs_Cutin + screen banner)
  drag / throw      Formation_Pickup, pendulum swing, gravity, bounce, squash
  ragdoll           right-click -> Ragdoll: she goes limp, tumbles, lands on the floor
                    or a window top, flops about, lies there, then stands back up.
                    Two cheap layers (see the RAG_* block), no physics engine.
  Ctrl + 1          the same, from anywhere in Windows, aimed at whoever is under
                    the cursor. A character also gets a short sound; bare desktop
                    is silent.
  blow mode         double-middle-click gusts wind from the cursor (hair physics)
  stroke over head  petting (move the cursor back and forth over her head)
  wheel             resize      shift+wheel / middle-drag / ctrl+drag  spin 3D
  right-click       menu for that character   tray icon + panel for the rest
  (Windows) they walk / jump / ride on top of your open windows, but only
            the ones on their screen

Picking the screen
  --screen N        use screen number N (0 = first in Qt's screen list)
  --screen cursor   use the screen the mouse is on at startup (default)
  --screen primary  use the primary screen
  Change it any time in the control panel (Characters tab).
"""
from __future__ import annotations

import sys, os, re, json, math, time, random, struct, base64, ctypes, argparse, traceback
from pathlib import Path
from urllib.parse import unquote
from collections import deque

if sys.platform.startswith("linux") and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "xcb"          # window positioning needs X11

try:
    import numpy as np
    import moderngl
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as _e:                           # pragma: no cover
    print("Missing dependency:", _e)
    print('Run:  pip install PySide6 moderngl numpy "pymunk>=7,<8"')
    sys.exit(1)

# The articulated ragdoll is optional: without pymunk every pet falls back to the
# legacy rod ragdoll below, so the app still runs (cfg ragdoll_engine="legacy").
try:
    import ragphys
    from ragphys import RagWorld, HAVE_PYMUNK
except ImportError as _e:                           # pragma: no cover
    ragphys = None
    HAVE_PYMUNK = False
    print("pymunk not available, falling back to the rod ragdoll:", _e)

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, QPoint, QSize

# Dialogue lives in its own package: the lines are data, the lookup is engine.py,
# pet-to-pet talking is conversation.py, computer-state tags are system_watcher.py.
# None of it imports PySide, so there is no cycle and it can be tested alone.
try:
    from dialogue import (DialogueEngine, ChatDirector, Conversation,
                          ConversationField, SystemWatcher, StateReactor)
except ImportError as _e:                           # pragma: no cover
    print("Missing package:", _e)
    print("dialogue/ must sit in the same folder as this script.")
    sys.exit(1)

HERE = Path(__file__).resolve().parent
SETTINGS_PATH = HERE / "multi_single_screen_pet_ba_settings.json"   # its own settings file
MODEL_DIR = HERE / "model"          # every *.glb in here becomes a character
BASE_W, BASE_H = 380, 520
BAKE_FPS = 60

# Audio is optional: the pets never need it, so a Qt build without the multimedia
# plugins must still start (it just loses the "nothing under the cursor" sound).
try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    HAVE_AUDIO = True
except ImportError:                                # pragma: no cover
    QAudioOutput = QMediaPlayer = None
    HAVE_AUDIO = False

# Played when Ctrl+1 lands on a character, confirming the hit. Looked for next to
# this script first, then in sounds\, then as a plain path.
RAG_SOUND = "trimmed.mp3"
RAG_SOUND_VOLUME = 0.7

# key -> (animation name, loops?)
ANIM_KEYS = {
    "idle":     ("Cafe_Idle", True),
    "walk":     ("Cafe_Walk", True),
    "alert":    ("Formation_Idle", True),
    "react":    ("Cafe_Reaction", False),
    "pickup":   ("Formation_Pickup", False),
    "cutin":    ("Exs_Cutin", False),
    "tactical": ("Tactical_Start", False),
}

DEFAULTS = dict(
    scale=1.0, walk_speed=70.0, fps=30, always_on_top=True, wander=True,
    look_at_cursor=True, follow_cursor=False, perch=True, speech=True,
    particles=True, click_through=True, yaw_offset=0.0, pos_x=None,
    positions={},            # character name -> saved feet X
    hidden=[],               # character names the user turned off
    peer_look=True,          # occasionally look at another character
    peer_look_rate=0.35,     # 0..1  how often she glances over
    cursor_gaze_range=3.0,   # how many body-widths away the cursor still gets noticed
    cursor_gaze_far=0.10,    # 0..1  chance of still looking at it from far beyond that
    chat=True,               # characters talk to each other
    chat_rate=0.5,           # 0..1  chattiness
    chat_meetup=True,        # walk over before talking
    screen="cursor",         # "cursor" | "primary" | "0", "1", ...  (one screen only)
    preset="Blue Archive",
    toon_threshold=0.46, toon_softness=0.07, shadow_strength=0.62, exposure=1.0,
    deep_shadow=0.25, rim=0.45, spec=0.05, saturation=1.08, outline_px=1.5, outline_dark=0.34,
    eyes_through_bangs=False,   # C4: off by default, see Renderer.draw
    light_follow=False, light_az=-30.0, light_el=38.0,
    blow_mode=False,            # double-middle-click = gust of wind from cursor
    blow_strength=1.0,          # 0.2..2.5 gust power multiplier
    ragdoll_auto_getup=True,    # she stands back up by herself after lying still
    ragdoll_engine="pymunk",    # "pymunk" = articulated ragdoll, "legacy" = the old rod
    ragdoll_debug=False,        # draw the capsules, joints and window colliders over her
    ragdoll_self_collide=False,  # her own limbs collide with each other. Looks better (she
                                  # curls into a ball instead of lying flat with her arms
                                  # through her own torso) but roughly doubles the physics
                                  # cost: 4 ragdolls + 10 windows go 1.7 -> 3.5 ms/frame.
)

PRESETS = {
    "Blue Archive":   dict(toon_threshold=.46, toon_softness=.07,  shadow_strength=.62, exposure=1.0, deep_shadow=.25, rim=.45, spec=.05, saturation=1.08, outline_px=1.5, outline_dark=.34),
    "Soft Pastel":    dict(toon_threshold=.38, toon_softness=.14,  shadow_strength=.30, exposure=1.05, deep_shadow=.10, rim=.75, spec=.05, saturation=.95,  outline_px=1.1, outline_dark=.55),
    "Hard Cel":       dict(toon_threshold=.55, toon_softness=.006, shadow_strength=.85, exposure=1.0, deep_shadow=.35, rim=.35, spec=.30, saturation=1.20, outline_px=2.6, outline_dark=.15),
    "Night Patrol":   dict(toon_threshold=.52, toon_softness=.05,  shadow_strength=.90, exposure=.72, deep_shadow=.40, rim=1.10, spec=.20, saturation=.90, outline_px=2.0, outline_dark=.20),
    "Flat / Unshaded":dict(toon_threshold=.10, toon_softness=.05,  shadow_strength=.0,  exposure=1.0, deep_shadow=.0,  rim=.0,  spec=.0,  saturation=1.05, outline_px=1.5, outline_dark=.35),
}

# ------------------------------------------------------------------ dialogue
# Every word a pet can say lives in the dialogue package next to this file, so
# it can be edited without touching any Python here:
#
#   dialogue/engine.py          "give me a line for (character, event, states)"
#   dialogue/conversation.py    pet-to-pet talking
#   dialogue/system_watcher.py  what the computer is doing -> state tags
#   dialogue/data/*.json        the lines themselves
#
# Pet.line("poke") is the only thing the pet code needs to say something.
# ------------------------------------------------------------------ hold physics
# Test feature: while you hold / throw her, hair + coat + skirt + tie + limbs
# lag behind the window on damped springs, so she feels carried, not pasted
# onto the cursor. Purely procedural + additive: the baked clip still plays,
# we only add small post rotations (same mechanism as head look-at).
# Per-chain spring state lives on Rig, drive (window velocity) on Pet.
HOLD_JIGGLE_WORDS = ("hair", "coat", "skirt", "necktai", "necktie", "sleeve",
                      "rebon", "ribbon", "tail", "scarf", "tie", "ahoge")
HOLD_SKIP_WORDS = ("halo", "glow", "effect", "prop", "weapon", "fire", "book", "holster")
HOLD_DANGLE_WORDS = ("upperarm", "forearm", "thigh", "calf", "hand", "foot")
HOLD_GAIN = {"hair": 1.0, "cloth": 0.7, "tie": 0.9, "limb": 0.45}

# ------------------------------------------------------------------ wind gust
# Double-middle-click (with Blow mode on) bursts air out of the cursor in all
# directions. Each pet converts it to a radial push on the same hold springs
# the drag uses, so hair/coat/skirt flutter away from the cursor and settle.
GUST_DUR, GUST_RADIUS, GUST_SPEED = 0.9, 650.0, 2600.0
WIND_KINDS = ("hair", "cloth", "tie")  # gust bends these chains, never body/limbs

# ------------------------------------------------------------------- ragdoll
# Two engines, chosen per pet by cfg["ragdoll_engine"]:
#
#   "pymunk" (default) - the articulated ragdoll in ragphys.py. One capsule body
#      per limb, pinned and angle-limited against its parent, colliding with the
#      screen edges, the other pets and the real Windows. Every tunable lives in
#      the RAGP_* block there. She maps back onto the rig with one `post`
#      rotation per body (see rag_limb_post).
#
#   "legacy" - the original two-layer ragdoll, kept working unchanged:
#   1. the whole body is ONE rigid rod (head end <-> foot end) in screen space.
#      Gravity, walls, ceiling and floor / window-top contacts are solved with
#      plain impulses. The rod's position moves her window; its angle rotates
#      the model about her pelvis. Tumbling, bouncing, sliding, lying flat.
#   2. every limb (and the head) is a damped pendulum - one angle and one
#      velocity driven by apparent gravity, the body's acceleration and its spin.
#      The result is fed to the rig's existing `post` rotation list.
# Hair / skirt / tie need nothing: they already ride the hold-physics springs,
# and either engine keeps self.vx / self.vy equal to the body she is lying on.
RAG_REST_TIME = 3.0        # seconds lying still before she gets up on her own
RAG_GETUP_TIME = 0.7
RAG_COM_FRAC = 0.5         # fallback COM height as a fraction of her pixel height
RAG_COM_PAD = 0.05         # body thickness as a fraction of the window height
RAG_REST_E, RAG_FRICTION = 0.35, 0.6
# (bone, child bone, joint limit rad, return-to-pose stiffness, damping)
# The child is only used to measure the limb's on-screen direction and length.
# The damping numbers are roughly 0.6 * 2 * sqrt(gravity gain + stiffness) for each
# limb, i.e. a well-damped swing: enough to flop and whip, not enough to trampoline
# itself into the joint limits every time she hits something.
RAG_LIMBS = [
    ("l upperarm", "l forearm", 2.4, 3.0, 8.0), ("l forearm", "l hand", 1.6, 5.0, 10.0),
    ("r upperarm", "r forearm", 2.4, 3.0, 8.0), ("r forearm", "r hand", 1.6, 5.0, 10.0),
    ("l thigh", "l calf", 1.5, 6.0, 9.0),       ("l calf", "l foot", 1.2, 8.0, 9.5),
    ("r thigh", "r calf", 1.5, 6.0, 9.0),       ("r calf", "r foot", 1.2, 8.0, 9.5),
    ("neck", "head", 0.7, 10.0, 11.0),          # head lags behind the body
]

def rot_axis(axis, ang):
    """Rodrigues: 3x3 rotation of `ang` radians about unit `axis` (right-handed)."""
    x, y, z = axis; c, s = math.cos(ang), math.sin(ang); t = 1 - c
    return np.array([[t*x*x + c,   t*x*y - s*z, t*x*z + s*y],
                     [t*x*y + s*z, t*y*y + c,   t*y*z - s*x],
                     [t*x*z - s*y, t*y*z + s*x, t*z*z + c]])

TWRAP = int(getattr(Qt.TextFlag.TextWordWrap, "value", Qt.TextFlag.TextWordWrap))


# =============================================================================
#  math helpers (column-vector convention, numpy 4x4)
# =============================================================================
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)

def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)

def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)

def m4(R=None, t=None):
    M = np.eye(4)
    if R is not None: M[:3, :3] = R
    if t is not None: M[:3, 3] = t
    return M

def translate(t):
    return m4(None, np.asarray(t, dtype=np.float64))

def rot_z4(a):
    M = np.eye(4); c, s = math.cos(a), math.sin(a)
    M[0, 0] = c; M[0, 1] = -s; M[1, 0] = s; M[1, 1] = c
    return M

def scale4(x, y, z):
    return np.diag([x, y, z, 1.0])

def perspective(fovy, aspect, n, f):
    t = 1.0 / math.tan(fovy / 2.0)
    M = np.zeros((4, 4))
    M[0, 0] = t / aspect; M[1, 1] = t
    M[2, 2] = (f + n) / (n - f); M[2, 3] = 2 * f * n / (n - f); M[3, 2] = -1.0
    return M

def mat_bytes(M):
    return np.ascontiguousarray(np.asarray(M, dtype=np.float32).T).tobytes()

def quat_to_rot(q):
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.empty((len(q), 3, 3))
    R[:, 0, 0] = 1 - 2 * (y * y + z * z); R[:, 0, 1] = 2 * (x * y - z * w); R[:, 0, 2] = 2 * (x * z + y * w)
    R[:, 1, 0] = 2 * (x * y + z * w); R[:, 1, 1] = 1 - 2 * (x * x + z * z); R[:, 1, 2] = 2 * (y * z - x * w)
    R[:, 2, 0] = 2 * (x * z - y * w); R[:, 2, 1] = 2 * (y * z + x * w); R[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return R

def compose_trs(T, Q, S):
    n = len(T)
    M = np.zeros((n, 4, 4))
    M[:, :3, :3] = quat_to_rot(Q) * S[:, None, :]
    M[:, :3, 3] = T
    M[:, 3, 3] = 1.0
    return M

def pelvis_track(chain, T, Q, S):
    """World position of the pelvis bone through every frame of a clip.

    Only the ancestor chain is composed (a handful of nodes) instead of the whole
    skeleton, so this stays cheap even for long clips.
    """
    W = None
    for nd in chain:                                  # T/Q/S are (F, n_nodes, ...)
        q, t, s = Q[:, nd], T[:, nd], S[:, nd]
        L = np.zeros((len(t), 4, 4))
        L[:, :3, :3] = quat_to_rot(q) * s[:, None, :]
        L[:, :3, 3] = t
        L[:, 3, 3] = 1.0
        W = L if W is None else np.matmul(W, L)
    return W[:, :3, 3]                               # (F, 3)

def mat_to_quat(R):
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2; w = .25 * s
        x = (R[2, 1] - R[1, 2]) / s; y = (R[0, 2] - R[2, 0]) / s; z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s; x = .25 * s; y = (R[0, 1] + R[1, 0]) / s; z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s; x = (R[0, 1] + R[1, 0]) / s; y = .25 * s; z = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s; x = (R[0, 2] + R[2, 0]) / s; y = (R[1, 2] + R[2, 1]) / s; z = .25 * s
    q = np.array([x, y, z, w]); return q / np.linalg.norm(q)

def decompose(m):
    t = m[:3, 3].copy()
    sc = np.linalg.norm(m[:3, :3], axis=0)
    sc[sc < 1e-12] = 1.0
    if np.linalg.det(m[:3, :3]) < 0: sc[0] = -sc[0]
    return t, mat_to_quat(m[:3, :3] / sc), sc

def slerp(q0, q1, t):
    d = np.sum(q0 * q1, axis=-1, keepdims=True)
    q1 = np.where(d < 0, -q1, q1)
    d = np.clip(np.abs(d), 0.0, 1.0)
    th = np.arccos(d); sn = np.sin(th)
    near = sn < 1e-5
    den = np.where(near, 1.0, sn)
    a = np.where(near, 1.0 - t, np.sin((1.0 - t) * th) / den)
    b = np.where(near, t, np.sin(t * th) / den)
    q = a * q0 + b * q1
    return q / np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-12)


# =============================================================================
#  glTF / GLB loader
# =============================================================================
COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}

class GLTF:
    def __init__(self, path):
        path = Path(path)
        data = path.read_bytes()
        magic, _ver, _len = struct.unpack_from("<4sII", data, 0)
        if magic != b"glTF":
            raise ValueError("Not a binary glTF (.glb) file")
        off, self.bin, self.j = 12, b"", None
        while off + 8 <= len(data):
            clen, ctype = struct.unpack_from("<II", data, off); off += 8
            chunk = data[off:off + clen]; off += clen
            if ctype == 0x4E4F534A: self.j = json.loads(chunk.decode("utf-8"))
            elif ctype == 0x004E4942: self.bin = chunk
        self.dir = path.parent
        self._bufs = {}

    def buffer(self, i):
        if i in self._bufs: return self._bufs[i]
        b = self.j["buffers"][i]
        uri = b.get("uri")
        if uri is None: data = self.bin
        elif uri.startswith("data:"): data = base64.b64decode(uri.split(",", 1)[1])
        else: data = (self.dir / unquote(uri)).read_bytes()
        self._bufs[i] = data
        return data

    def accessor(self, idx):
        a = self.j["accessors"][idx]
        dt = np.dtype(COMP[a["componentType"]]); n = NCOMP[a["type"]]; count = a["count"]
        if "bufferView" in a:
            bv = self.j["bufferViews"][a["bufferView"]]
            buf = self.buffer(bv["buffer"])
            start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
            esz = dt.itemsize * n
            stride = bv.get("byteStride") or esz
            if stride == esz:
                arr = np.frombuffer(buf, dtype=dt, count=count * n, offset=start).reshape(count, n)
            else:
                raw = np.frombuffer(buf, dtype=np.uint8)
                ix = start + np.arange(count)[:, None] * stride + np.arange(esz)[None, :]
                arr = np.ascontiguousarray(raw[ix]).view(dt).reshape(count, n)
        else:
            arr = np.zeros((count, n), dtype=dt)
        if a.get("normalized"):
            arr = arr.astype(np.float32)
            ct = a["componentType"]
            arr = {5120: np.maximum(arr / 127.0, -1), 5121: arr / 255.0,
                   5122: np.maximum(arr / 32767.0, -1), 5123: arr / 65535.0}.get(ct, arr)
        return arr.reshape(count) if n == 1 else arr

    def image_bytes(self, idx):
        im = self.j["images"][idx]
        if "bufferView" in im:
            bv = self.j["bufferViews"][im["bufferView"]]
            buf = self.buffer(bv["buffer"]); o = bv.get("byteOffset", 0)
            return bytes(buf[o:o + bv["byteLength"]])
        uri = im["uri"]
        if uri.startswith("data:"): return base64.b64decode(uri.split(",", 1)[1])
        return (self.dir / unquote(uri)).read_bytes()


def toks(name):
    return [t for t in re.split(r"[\s_\.\:\|\-]+", name.lower()) if t]

def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())

def compute_normals(pos, idx):
    tri = idx.reshape(-1, 3)
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    fn = np.cross(p1 - p0, p2 - p0)
    n = np.zeros_like(pos, dtype=np.float64)
    for k in range(3): np.add.at(n, tri[:, k], fn)
    l = np.linalg.norm(n, axis=1, keepdims=True); l[l < 1e-12] = 1
    return (n / l).astype(np.float32)

def smooth_normals(pos, nrm):
    """Average normals of coincident vertices (so the outline hull has no cracks)."""
    ext = float((pos.max(0) - pos.min(0)).max()) or 1.0
    q = np.round((pos - pos.min(0)) / (ext * 1e-5)).astype(np.int64)
    key = (q[:, 0] << 34) | (q[:, 1] << 17) | q[:, 2]
    _, inv = np.unique(key, return_inverse=True)
    inv = inv.reshape(-1)
    acc = np.zeros((inv.max() + 1, 3), dtype=np.float64)
    np.add.at(acc, inv, nrm.astype(np.float64))
    out = acc[inv]
    l = np.linalg.norm(out, axis=1, keepdims=True); l[l < 1e-12] = 1
    return (out / l).astype(np.float32)


def bleed_alpha(buf, w, h, iters=6):
    """B2: flood RGB of fully transparent texels outwards, so mipmaps don't
    drag black into hair cards and lashes (dark halo)."""
    a = np.frombuffer(buf, np.uint8).reshape(h, w, 4).copy()
    solid = a[..., 3] > 8
    if solid.all() or not solid.any(): return buf
    rgb = a[..., :3].astype(np.float32)
    for _ in range(iters):
        acc = np.zeros_like(rgb); cnt = np.zeros(solid.shape, np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            s = np.roll(solid, (dy, dx), (0, 1)); acc += np.roll(rgb, (dy, dx), (0, 1)) * s[..., None]; cnt += s
        fill = (~solid) & (cnt > 0)
        rgb[fill] = acc[fill] / cnt[fill, None]; solid |= fill
    a[..., :3] = rgb.astype(np.uint8)
    return a.tobytes()


class Clip:
    def __init__(self, name, duration, T, Q, S, Wt=None):
        self.name, self.duration, self.T, self.Q, self.S = name, duration, T, Q, S
        self.Wt = Wt                              # (frames, nodes) morph-target weight 0 per node
        self.drift = None
        self.ssig = None                          # log-scale signature of the structural bones
        self.pin = np.zeros((len(T), 3))     # per-frame offset that keeps the pelvis planted
        self.origin = np.zeros(3)            # frame-0 offset onto the idle pivot


class Skin:
    pass

class Prim:
    pass


def sample_channel(times, vals, interp, ts, path):
    F = len(ts)
    if interp == "CUBICSPLINE" and len(vals) == 3 * len(times):
        vals = vals[1::3]
    if len(times) == 1:
        return np.repeat(vals[:1], F, axis=0)
    idx = np.clip(np.searchsorted(times, ts, side="right") - 1, 0, len(times) - 2)
    t0, t1 = times[idx], times[idx + 1]
    u = np.clip((ts - t0) / np.maximum(t1 - t0, 1e-9), 0, 1)[:, None]
    if interp == "STEP": u = np.zeros_like(u)
    a, b = vals[idx], vals[idx + 1]
    if path == "rotation": return slerp(a, b, u)
    return a + (b - a) * u


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

        # ---- animations (baked to fixed rate) ----
        self.clips = {}
        for ai, a in enumerate(j.get("animations", [])):
            name = a.get("name", f"anim{ai}")
            chans, dur = [], 0.0
            for ch in a["channels"]:
                tg = ch["target"]
                if "node" not in tg or tg["path"] not in ("translation", "rotation", "scale", "weights"): continue
                sm = a["samplers"][ch["sampler"]]
                times = g.accessor(sm["input"]).astype(np.float64).reshape(-1)
                vals = g.accessor(sm["output"]).astype(np.float64)
                chans.append((tg["node"], tg["path"], times, vals, sm.get("interpolation", "LINEAR")))
                dur = max(dur, float(times[-1]))
            dur = max(dur, 1 / 30)
            F = max(2, int(math.ceil(dur * BAKE_FPS)) + 1)
            ts = np.linspace(0, dur, F)
            cT = np.repeat(T[None], F, 0); cQ = np.repeat(Q[None], F, 0); cS = np.repeat(S[None], F, 0)
            cW = np.repeat(self.node_w0[None], F, 0)
            for node, path, times, vals, interp in chans:
                if path == "weights":
                    nt = max(1, vals.size // (len(times) * (3 if interp == "CUBICSPLINE" else 1)))
                    cW[:, node] = sample_channel(times, vals.reshape(-1, nt), interp, ts, path)[:, 0]
                    continue
                v = sample_channel(times, vals, interp, ts, path)
                if path == "translation": cT[:, node] = v
                elif path == "rotation": cQ[:, node] = v
                else: cS[:, node] = v
            self.clips[name] = Clip(name, dur, cT, cQ, cS, cW)
        self.rest_clip = Clip("rest", 1.0, np.repeat(T[None], 2, 0), np.repeat(Q[None], 2, 0), np.repeat(S[None], 2, 0),
                               np.repeat(self.node_w0[None], 2, 0))
        for c in list(self.clips.values()) + [self.rest_clip]:
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
        idle_ref = self.find_clip("Cafe_Idle") or self.rest_clip
        ref = pelvis_track(self.pelvis_chain, idle_ref.T[0:1], idle_ref.Q[0:1], idle_ref.S[0:1])[0].copy()
        for c in list(self.clips.values()) + [self.rest_clip]:
            c.origin = ref - pelvis_track(self.pelvis_chain, c.T[0:1], c.Q[0:1], c.S[0:1])[0]

        # ---- B2: only bleed textures used exclusively by MASK/BLEND prims ----
        used_a = {p.src for p in self.prims if p.amode != 0}
        used_o = {p.src for p in self.prims if p.amode == 0}
        self.bleed_srcs = used_a - used_o

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
            qi = QtGui.QImage.fromData(self.g.image_bytes(src))
            if qi.isNull(): raise ValueError("decode failed")
            qi = qi.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
            w, h, bpl = qi.width(), qi.height(), qi.bytesPerLine()
            buf = bytes(qi.constBits())
            if bpl != w * 4:
                buf = np.frombuffer(buf, np.uint8).reshape(h, bpl)[:, : w * 4].tobytes()
            res = (w, h, buf[: w * h * 4])
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
        return self.clips[min(c, key=len)] if c else None

    def pose_world(self, T, Q, S, post=None):
        L = compose_trs(T, Q, S); W = np.empty_like(L)
        pm = {}
        if post:
            for node, R in post: pm.setdefault(int(self.depth[node]), []).append((node, R))
        for d, idx in enumerate(self.levels):
            if d == 0: W[idx] = L[idx]
            else: W[idx] = np.matmul(W[self.parent[idx]], L[idx])
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
                sk = self.skins[p.skin]; bm = np.matmul(W[sk.joints], sk.ibm)
                acc = np.zeros((len(ph), 4))
                for k in range(4):
                    ji = np.clip(p.joints[:, k].astype(np.int64), 0, len(bm) - 1)
                    acc += p.weights[:, k:k + 1] * np.einsum("vij,vj->vi", bm[ji], ph)
            else:
                acc = ph @ W[p.node].T
            out.append(acc[:, :3])
        return np.concatenate(out)

    def compute_fit(self, W):
        pts = self.posed_positions(W, skip_fx=True)
        if len(pts) == 0: pts = self.posed_positions(W)      # everything is an effect prim
        lo, hi = np.percentile(pts, 0.2, axis=0), np.percentile(pts, 99.8, axis=0)
        pel = W[self.pelvis][:3, 3]
        return dict(cx=float(pel[0]), cz=float(pel[2]), miny=float(lo[1]), maxy=float(hi[1]), H=float(hi[1] - lo[1]))

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


# =============================================================================
#  Animation player with cross-fade
# =============================================================================
class Rig:
    def __init__(self, model):
        self.m = model; self.clip = None; self.t = 0.0; self.loop = True; self.speed = 1.0
        self.done = False; self.fade = 1.0; self.fade_dur = 0.15; self.prev = None; self.cur_pose = None
        self.world = model.W0
        self.world_base = model.W0        # same pose with the ragdoll rotations removed
        self.cur_wt = model.node_w0
        # hold-physics spring state: per jiggle chain [side, front, side-vel, front-vel]
        # (radians of lag). One extra slot drives the dangling limbs together.
        self.hold_n = len(getattr(model, "jiggle_chains", []))
        self.hold = [[0.0, 0.0, 0.0, 0.0] for _ in range(self.hold_n + 1)]
        self.hold_yaw = 0.0
        # ragdoll: limb-pendulum post rotations, written by Pet every frame
        self.rag_post = []
        # Pet turns this off while she is limp - the "limbs dangle together"
        # spring below would otherwise fight the per-limb pendulums.
        self.dangle_on = True
        # Limp = the articulated ragdoll owns the pose. Set by Pet on go-limp,
        # cleared when she is standing again. While it is set:
        #   - advance() is a no-op, so the clip cannot play (this is what used
        #     to happen: nudging a ragdolled pet started an animation),
        #   - play() is refused, so nothing can restart one either,
        #   - evaluate() drops the head-look rotations.
        self.limp = False

    def set_limp(self, on):
        """Freeze the rig into (or out of) the limp pose.

        Freezing needs the clip to be HELD, not merely paused: `advance()` with
        speed 0 keeps `t` where it is, but any later `play()` restarts it. So
        while limp the rig refuses clip changes outright and `rag_freeze_pose`
        parks the current clip on its last frame.
        """
        if on and not self.limp:
            c = self.clip
            if c is not None:
                self.speed = 0.0; self.t = c.duration; self.done = True
                self.fade = 1.0; self.prev = None
        elif not on:
            self.speed = 1.0; self.done = False
        self.limp = on

    def play(self, clip, loop, speed=1.0, fade=0.15, restart=True):
        if clip is None or self.limp: return    # nothing may restart a limp body
        if (not restart) and clip is self.clip:
            self.loop, self.speed = loop, speed; return
        self.prev = self.cur_pose
        self.fade = 0.0 if (self.prev is not None and fade > 0) else 1.0
        self.fade_dur = max(fade, 1e-3)
        # A hard cut beats sliding through garbage when the two clips disagree about
        # bone scale (see Model: c.ssig).
        if self.prev is not None and self.clip is not None \
                and clip.ssig is not None and self.clip.ssig is not None \
                and float(np.abs(clip.ssig - self.clip.ssig).max()) > 0.5:
            self.prev = None; self.fade = 1.0
        self.clip, self.t, self.loop, self.speed, self.done = clip, 0.0, loop, speed, False

    def advance(self, dt):
        c = self.clip
        if c is None or self.limp: return      # a limp body does not animate
        self.t += dt * self.speed
        if self.loop:
            if c.duration > 0: self.t %= c.duration
        elif self.t >= c.duration:
            self.t = c.duration; self.done = True
        if self.fade < 1.0: self.fade = min(1.0, self.fade + dt / self.fade_dur)

    def sample(self):
        """Current pose + its root-motion offset.

        Nothing is blended here. Cross-fading happens in *world* space in evaluate():
        lerping local TRS only works when the hierarchy is a plain chain, and Iroha's
        is not - her pelvis is a descendant of her head bone and 'character_root'
        carries a 364-unit local translation that only cancels once composed. Blending
        those locals sends the pelvis several body heights off screen.
        """
        c = self.clip; F = c.T.shape[0]
        f = min(self.t / c.duration, 1.0) * (F - 1) if c.duration > 0 else 0.0
        i0 = min(int(f), F - 2); u = f - i0
        T = c.T[i0] * (1 - u) + c.T[i0 + 1] * u
        S = c.S[i0] * (1 - u) + c.S[i0 + 1] * u
        Q = slerp(c.Q[i0], c.Q[i0 + 1], np.full((c.Q.shape[1], 1), u))
        # A1: the root-motion pin rides along with the pose so it cross-fades too,
        # instead of snapping on the first frame of the new clip.
        off = c.origin + c.pin[i0] * (1 - u) + c.pin[i0 + 1] * u
        self.cur_pose = (T, Q, S, off)
        if c.Wt is not None: self.cur_wt = c.Wt[i0]
        return T, Q, S, off

    def update_hold(self, dt, vx, vy, energy, yaw=0.0, wx=0.0, wy=0.0, we=0.0):
        """Drive the secondary-motion springs from window velocity (px/s).

        vx/vy = smoothed window velocity, energy = 0..1 how "held" she is
        (1 while dragging, decays after release). yaw = model facing so screen
        motion maps into model space. Always integrated (so it settles softly)
        but targets collapse to ~0 when energy is 0, leaving baked anim alone.
        wx/wy/we = gust wind (px/s + 0..1 energy). Wind bends only WIND_KINDS
        (hair + clothes) - body and limbs never see it.
        """
        m = self.m
        if not getattr(m, "jiggle_chains", None) and not getattr(m, "dangle_bones", None):
            return
        dt = clamp(dt, 1e-4, 0.05)
        self.hold_yaw = yaw
        # screen -> model space (model faces +Z at yaw 0; screen X is camera X)
        c, s = math.cos(yaw), math.sin(yaw)
        lat = (vx * c) * energy
        dep = (vx * s) * energy
        fwd = (-vy * c) * energy
        wlat = wx * c                    # wind already carries its own envelope + falloff
        wdep = wx * s
        wfwd = -wy * c
        # gentle idle breeze so hair never looks glued when parked
        t = time.time()
        breeze = 0.02 * energy + 0.004
        targets = []
        for ci, ch in enumerate(list(m.jiggle_chains) + [dict(nodes=[], kind="limb")]):
            kind = ch.get("kind", "cloth")
            g = HOLD_GAIN.get(kind, 0.7)
            wob = 0.8 + 0.4 * ((ci * 37) % 10) / 10.0     # desync chains
            wind = 1.0 if kind in WIND_KINDS else 0.0
            kx, kf = 0.00045 * g * wob, 0.00038 * g * wob
            # drag lags BEHIND the motion (negative); wind pushes WITH the air (positive)
            tx = clamp(-lat * kx + wlat * wind * kx, -0.85, 0.85)
            ty = clamp(-fwd * kf + dep * kf + (wfwd - wdep) * wind * kf
                       + math.sin(t * 1.7 + ci * 1.3) * breeze * g, -0.70, 0.70)
            targets.append((tx, ty, kind))
        for (tx, ty, kind), st in zip(targets, self.hold):
            stiff = 70.0 if kind == "limb" else 95.0
            damp = 7.5 if kind == "limb" else 6.0
            for k in (0, 1):
                tgt = (tx, ty)[k]
                # critically-damped-ish spring; dep adds a touch of twist variety
                acc = stiff * (tgt - st[k]) - damp * st[k + 2]
                st[k + 2] += acc * dt
                st[k] += st[k + 2] * dt
                st[k] = clamp(st[k], -1.0, 1.0)

    def _hold(self):
        m = self.m; post = []
        if not getattr(m, "jiggle_chains", None) and not getattr(m, "dangle_bones", None):
            return post
        for ch, st in zip(m.jiggle_chains, self.hold):
            sx, sy = st[0], st[1]
            if abs(sx) < 1e-2 and abs(sy) < 1e-2: continue
            g = HOLD_GAIN.get(ch.get("kind", "cloth"), 0.7)
            for di, node in enumerate(ch["nodes"]):
                f = (0.45 + 0.55 * (di + 1) / max(1, len(ch["nodes"]))) * g
                post.append((int(node), rot_z(sx * f) @ rot_x(sy * f)))
        # limbs dangle together on the last spring slot, weaker + mirrored L/R
        # (off while she is ragdolled - Pet's own limb pendulums own the limbs)
        if getattr(m, "dangle_bones", None) and len(self.hold) and self.dangle_on:
            sx, sy = self.hold[-1][0], self.hold[-1][1]
            if abs(sx) > 1e-2 or abs(sy) > 1e-2:
                for node in m.dangle_bones:
                    nm = m.names[int(node)].lower()
                    mir = -1.0 if any(k in nm for k in ("r upperarm", "r forearm", "r thigh", "r calf", "r hand", "r foot")) else 1.0
                    amp = 0.55 if "arm" in nm or "hand" in nm else 0.35
                    post.append((int(node), rot_z(sx * amp * mir) @ rot_x(sy * amp)))
        return post

    def _look(self, yaw, pitch):
        m = self.m; post = []
        if m.look_bones and (abs(yaw) > 1e-4 or abs(pitch) > 1e-4):
            Rf = rot_y(m.face_yaw)                        # B6: pitch axis follows the model's facing
            for node, w in m.look_bones:
                post.append((node, rot_y(yaw * w) @ Rf.T @ rot_x(-pitch * w) @ Rf))
        return post

    def evaluate(self, yaw, pitch):
        m = self.m; T, Q, S, off = self.sample()
        m.node_w = self.cur_wt
        # Limp: the physics owns every bone, so the rig must contribute NOTHING
        # on top of it. Head-look is the one that showed up on screen - a
        # ragdolled pet whose eyes tracked the cursor, and whose clip was still
        # running underneath the physics, looked like it was playing an
        # animation while lying on the floor. The _hold() springs stay on: they
        # are hair and cloth, which should still hang and swing while she is
        # unconscious, and they are not bones the ragdoll drives.
        post = ([] if self.limp else self._look(yaw, pitch)) + self._hold()
        rag = self.rag_post
        W = m.pose_world(T, Q, S, post + rag)
        # The same pose with the ragdoll rotations left out. Pet measures every
        # limb's on-screen direction and length against it: measured against the
        # posed world instead, a swinging forearm would tilt the upper arm's own
        # pendulum, which tilts the forearm back, and the pair runs away into their
        # joint limits within a second.
        Wb = m.pose_world(T, Q, S, post) if rag else W.copy()
        if self.prev is not None and self.fade < 1.0:
            k = self.fade * self.fade * (3 - 2 * self.fade)
            pT, pQ, pS, pOff = self.prev
            # A2: a leaf bone scaled to 0 is a weapon/prop show-hide key, not an
            # animation. Hand the previous pose the current scale so it never ramps
            # through a visible grow/shrink (and so zero-scale normals can't go NaN).
            # Leaves only: structural bones are never show/hide keys.
            hide = np.minimum(pS, S).min(axis=1) < 0.05
            hide &= m.is_leaf
            if hide.any():
                pS = pS.copy(); pS[hide] = S[hide]
            Pb = m.pose_world(pT, pQ, pS, post)
            W = Pb * (1 - k) + W * k
            Wb = Pb * (1 - k) + Wb * k
            off = pOff * (1 - k) + off * k               # A1: offset cross-fades with the pose
        elif self.fade >= 1.0:
            self.prev = None
        W[:, :3, 3] += off
        Wb[:, :3, 3] += off
        self.world = W
        self.world_base = Wb
        return W


# =============================================================================
#  Renderer: moderngl, MSAA, GPU skinning, Blue Archive style toon shader
# =============================================================================
VS_COMMON = """
#version 330
uniform mat4 u_proj, u_view, u_model, u_node;
uniform sampler2D u_bones;
uniform int u_skinned, u_base;
in vec3 in_pos; in vec3 in_nrm; in vec2 in_uv; in vec4 in_joints; in vec4 in_weights; in vec3 in_onrm;
mat4 bone(int j){
    int r = u_base + j;
    return mat4(texelFetch(u_bones, ivec2(0, r), 0), texelFetch(u_bones, ivec2(1, r), 0),
                texelFetch(u_bones, ivec2(2, r), 0), texelFetch(u_bones, ivec2(3, r), 0));
}
mat4 skinMat(){
    if (u_skinned == 0) return u_node;
    return in_weights.x * bone(int(in_joints.x + 0.5)) + in_weights.y * bone(int(in_joints.y + 0.5))
         + in_weights.z * bone(int(in_joints.z + 0.5)) + in_weights.w * bone(int(in_joints.w + 0.5));
}
"""

VS_MAIN = VS_COMMON + """
out vec3 v_nrm; out vec3 v_wpos; out vec2 v_uv;
void main(){
    mat4 sk = skinMat();
    vec4 wp = u_model * sk * vec4(in_pos, 1.0);
    vec3 n = mat3(u_model) * mat3(sk) * in_nrm;
    float nl = length(n);
    n = nl > 1e-5 ? n / nl : vec3(0.0);      // zero-scale bone would give NaN
    v_nrm = n; v_wpos = wp.xyz; v_uv = in_uv;
    gl_Position = u_proj * u_view * wp;
}
"""

FS_MAIN = """
#version 330
uniform sampler2D u_tex;
uniform vec4 u_basecol;
uniform int u_alpha_mode;
uniform float u_cutoff, u_shade, u_fade;
uniform vec3 u_light, u_campos, u_light_col, u_shadow_tint, u_rim_col;
uniform float u_thresh, u_soft, u_shadow, u_rim, u_spec, u_sat, u_deep;
in vec3 v_nrm; in vec3 v_wpos; in vec2 v_uv;
out vec4 f_col;

vec3 shadowOf(vec3 c){
    vec3 s = mix(c, c * c, 0.55);               // deeper but keeps saturation
    return mix(c, s * u_shadow_tint, u_shadow);
}
void main(){
    vec4 t = texture(u_tex, v_uv) * u_basecol;
    if (u_alpha_mode == 1 && t.a < u_cutoff) discard;
    if (u_alpha_mode == 2 && t.a < 0.06) discard;
    // cutout is all-or-nothing: letting partial alpha through wrote depth on
    // edge fragments and left a faint see-through fringe where hair overlaps skin
    float a = ((u_alpha_mode == 2) ? t.a : 1.0) * u_fade;
    vec3 base = t.rgb;
    float nl = length(v_nrm);
    vec3 N = nl > 1e-5 ? v_nrm / nl : vec3(0.0, 0.0, 1.0);
    vec3 V = normalize(u_campos - v_wpos);
    vec3 L = normalize(u_light);
    float ndl = dot(N, L);
    float hl = mix(1.0, ndl * 0.5 + 0.5, u_shade);
    float lit  = smoothstep(u_thresh - u_soft, u_thresh + u_soft, hl);
    float deep = smoothstep(u_thresh * 0.45 - u_soft, u_thresh * 0.45 + u_soft, hl);
    vec3 sh = shadowOf(base);
    vec3 col = mix(mix(sh, shadowOf(sh), u_deep), sh, deep);    // u_deep = 0 disables the 3rd band
    col = mix(col, base * u_light_col, lit);
    float fres = 1.0 - clamp(dot(N, V), 0.0, 1.0);
    float rim = smoothstep(0.62, 0.85, fres) * smoothstep(-0.15, 0.45, ndl) * u_rim * u_shade;
    col += (1.0 - col) * u_rim_col * rim * 0.6;                 // screen-style, never clips
    vec3 H = normalize(L + V);
    col += vec3(smoothstep(0.50, 0.55, pow(max(dot(N, H), 0.0), 48.0))) * u_spec * u_shade * u_light_col;
    float l = dot(col, vec3(0.299, 0.587, 0.114));
    col = clamp(mix(vec3(l), col, u_sat), 0.0, 1.0);
    f_col = vec4(col * a, a);
}
"""

VS_OUT = VS_COMMON + """
uniform float u_width;
out vec2 v_uv;
void main(){
    mat4 sk = skinMat();
    vec4 wp = u_model * sk * vec4(in_pos, 1.0);
    vec3 n = mat3(u_model) * mat3(sk) * in_onrm;
    float nl = length(n);
    n = nl > 1e-5 ? n / nl : vec3(0.0);
    vec4 vp = u_view * wp;
    vec3 nh = mat3(u_model) * mat3(sk) * in_nrm;
    float nhl = length(nh);
    float facing = nhl > 1e-5 ? dot(nh / nhl, normalize(-vp.xyz)) : 0.0;
    vp.xy += n.xy * u_width;      // screen-space inflate along the smoothed normal
    vp.z  -= u_width * 2.0 + max(facing, 0.0) * u_width * 40.0;   // extra push where the surface
                                                                // faces us: kills speckles between
                                                                // layered cloth/hair without speckling
                                                                // mirrored bones like a winding test
    v_uv = in_uv;
    gl_Position = u_proj * vp;
}
"""

FS_OUT = """
#version 330
uniform sampler2D u_tex;
uniform vec4 u_basecol;
uniform int u_alpha_mode;
uniform float u_cutoff, u_dark;
in vec2 v_uv;
out vec4 f_col;
void main(){
    vec4 t = texture(u_tex, v_uv) * u_basecol;
    if (u_alpha_mode == 1 && t.a < u_cutoff) discard;
    vec3 c = mix(t.rgb, t.rgb * t.rgb, 0.6) * u_dark * 1.5 * vec3(0.92, 0.94, 1.0);
    f_col = vec4(c, 1.0);
}
"""

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
            data = np.hstack([p.pos, p.nrm, p.uv, p.joints, p.weights, p.onrm]).astype("f4")
            vbo = ctx.buffer(data.tobytes()); ibo = ctx.buffer(p.idx.astype("u4").tobytes())
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
        H = fit["H"]; V = H / self.FRAC
        self.dist = V / (2 * math.tan(self.FOV / 2))
        cy = fit["miny"] + V / 2 - self.PADB * V
        self.eye = np.array([fit["cx"], cy, fit["cz"] + self.dist])
        self.view = translate(-self.eye)
        self.V_world = V
        self.PV = np.eye(4)

    def set_size(self, w, h):
        if (w, h) == (self.w, self.h): return
        ctx = self.ctx
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
        d = self.dist; H = self.fit["H"]
        self.proj = perspective(self.FOV, w / h, max(d - H * 2.0, d * 0.05), d + H * 3.0)
        self.world_per_px = self.V_world / h

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
        for pg in (self.pg, self.po):
            U(pg, "u_proj", mat_bytes(self.proj)); U(pg, "u_view", mat_bytes(self.view)); U(pg, "u_model", mat_bytes(M))
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
        opaque = [p for p in self.prims if p.amode < 2 and shown(p)]
        blends = [p for p in self.prims if p.amode == 2 and shown(p)]
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
        for p in blends: self._draw(p, world, False)
        try: self.fbo.depth_mask = True
        except Exception: pass
        ctx.copy_framebuffer(self.fbo_rs, self.fbo)
        return self.fbo_rs.read(components=4, alignment=1)

    def _draw(self, p, world, outline, fade=1.0):
        prog = self.po if outline else self.pg
        p.tex.use(0)
        U(prog, "u_skinned", 1 if p.skin >= 0 else 0); U(prog, "u_base", int(p.base))
        U(prog, "u_node", mat_bytes(world[p.node]) if p.skin < 0 else mat_bytes(np.eye(4)))
        U(prog, "u_basecol", tuple(float(x) for x in p.basecol))
        U(prog, "u_alpha_mode", int(p.amode)); U(prog, "u_cutoff", float(p.cutoff))
        if not outline:
            U(prog, "u_shade", float(p.shade)); U(prog, "u_fade", float(fade))
        (p.vao_o if outline else p.vao).render()


# =============================================================================
#  Windows: top-of-window perches (ctypes, no extra deps)
# =============================================================================
class WinTracker:
    SKIP = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow",
            "XamlExplorerHostIslandWindow", "ApplicationFrameInputSinkWindow", "TopLevelWindowForOverflowXamlIsland"}

    def __init__(self):
        self.enabled = sys.platform == "win32"; self.wins = []
        if self.enabled:
            try:
                from ctypes import wintypes
                self.wt = wintypes
                self.u32 = ctypes.windll.user32; self.dwm = ctypes.windll.dwmapi
                self.u32.IsWindowVisible.argtypes = [wintypes.HWND]
                self.u32.IsIconic.argtypes = [wintypes.HWND]
                self.u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
                self.u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
                self.u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                self.u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                self.dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
                self.proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                self.pid = os.getpid()
            except Exception:
                self.enabled = False

    def refresh(self, dpr, top_limit):
        if not self.enabled: return
        wt, out = self.wt, []
        cls = ctypes.create_unicode_buffer(256)
        def cb(hwnd, _lp):
            try:
                if not self.u32.IsWindowVisible(hwnd) or self.u32.IsIconic(hwnd): return True
                if self.u32.GetWindowTextLengthW(hwnd) == 0: return True
                pid = wt.DWORD(0); self.u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == self.pid: return True
                self.u32.GetClassNameW(hwnd, cls, 256)
                if cls.value in self.SKIP: return True
                cloaked = ctypes.c_int(0)
                self.dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
                if cloaked.value: return True
                r = wt.RECT()
                if self.dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
                    self.u32.GetWindowRect(hwnd, ctypes.byref(r))
                l, t, rr, b = r.left / dpr, r.top / dpr, r.right / dpr, r.bottom / dpr
                if rr - l < 160 or b - t < 80 or t < top_limit + 60: return True
                out.append((int(hwnd) if hwnd else 0, l, t, rr, b))
            except Exception:
                pass
            return True
        try:
            self.u32.EnumWindows(self.proc(cb), 0)
            self.wins = out
        except Exception:
            self.enabled = False

    def rect(self, hwnd):
        for w in self.wins:
            if w[0] == hwnd: return w[1:]
        return None

    def surfaces(self, fx):
        res = []
        for i, (h, l, t, r, b) in enumerate(self.wins):
            if l + 6 <= fx <= r - 6:
                covered = any(l2 <= fx <= r2 and t2 <= t + 3 <= b2 for (_, l2, t2, r2, b2) in self.wins[:i])
                if not covered: res.append((t, h))
        return res


# =============================================================================
#  Global hotkey (system-wide, Windows)
# =============================================================================
# Ctrl+1 anywhere in Windows flops whatever character is under the cursor, so it
# works while you are typing in something else - which is the whole point, since
# the pets are click-through overlays that rarely hold keyboard focus.
#
# A WH_KEYBOARD_LL hook rather than RegisterHotKey: Ctrl+1 belongs to a browser or
# editor half the time, and RegisterHotKey can lose that race. The low-level hook
# sees the key before anything else does. It also swallows Ctrl+1 so the app under
# the cursor does not also act on it (browser zoom reset and friends).
#
# The callback does nothing but raise a flag. Windows silently unhooks a low-level
# hook that takes longer than LowLevelHooksTimeout to answer, so the real work
# happens in App.tick() one frame later.
HOTKEY_VK_ONE = (0x31, 0x61)                  # '1' on the top row, and the numpad 1
HOTKEY_VK_CTRL = (0x11, 0xA2, 0xA3)              # generic / left / right control
HOTKEY_VK_SHIFT = (0x10, 0xA0, 0xA1)
HOTKEY_VK_ALT = (0x12, 0xA4, 0xA5)
HOTKEY_VK_WIN = (0x5B, 0x5C)
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WH_KEYBOARD_LL = 13


class _KbdEvent(ctypes.Structure):
    """KBDLLHOOKSTRUCT. dwExtraInfo is ULONG_PTR, so this is 24 bytes on x64."""
    _fields_ = [("vkCode", ctypes.c_uint32), ("scanCode", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("extra", ctypes.c_size_t)]

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)


class GlobalHotkey(QtCore.QObject):
    """System-wide Ctrl+1. Raises `fired`; nothing else happens inside the hook."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fired = False
        self._proc = None; self._h = None
        self._down = set()                     # modifier keys currently held
        self.enabled = self.install()

    def _match(self, vk, wp):
        pressed = wp in (WM_KEYDOWN, WM_SYSKEYDOWN)
        mods = HOTKEY_VK_CTRL + HOTKEY_VK_SHIFT + HOTKEY_VK_ALT + HOTKEY_VK_WIN
        if vk in mods:
            self._down.add(vk) if pressed else self._down.discard(vk)
            return False
        # Ctrl + a top-row (or numpad) 1, with nothing else held so we do not
        # steal Ctrl+Shift+1, Alt+Ctrl+1 or Win+Ctrl+1 from whatever needs them.
        want_ctrl = any(v in self._down for v in HOTKEY_VK_CTRL)
        others = any(v in self._down for v in HOTKEY_VK_SHIFT + HOTKEY_VK_ALT + HOTKEY_VK_WIN)
        return want_ctrl and not others and vk in HOTKEY_VK_ONE

    def install(self):
        if sys.platform != "win32":
            return False
        try:
            u32 = ctypes.windll.user32
            u32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_uint32]
            u32.SetWindowsHookExW.restype = ctypes.c_void_p
            u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            u32.UnhookWindowsHookEx.restype = ctypes.c_bool
            u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
            u32.CallNextHookEx.restype = ctypes.c_ssize_t

            def cb(code, wparam, lparam):
                # Nothing in here may raise: ctypes would print a traceback per
                # keystroke and answer 0, which both spams the console and lets the
                # key through to the app underneath.
                try:
                    if code >= 0:
                        # lparam arrives as a plain int, so wrap it before casting.
                        vk = int(ctypes.cast(ctypes.c_void_p(lparam),
                                              ctypes.POINTER(_KbdEvent)).contents.vkCode)
                        if self._match(vk, wparam) and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                            self.fired = True
                            return 1                   # ours now: don't pass it on
                except Exception:
                    pass
                return u32.CallNextHookEx(None, code, wparam, lparam)

            self._proc = HOOKPROC(cb)                   # must stay referenced
            # hMod NULL is correct here: the procedure lives in this process and the
            # hook covers the whole desktop, not one thread.
            self._h = u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
            if not self._h:
                self._proc = None
                print("[warn] global Ctrl+1 hook refused by Windows; using the in-app shortcut only")
                return False
            print("[info] global hotkey ready: Ctrl+1 (any app, any time)")
            return True
        except Exception as e:
            print(f"[warn] global Ctrl+1 hook failed: {e}")
            return False

    def remove(self):
        if self._h:
            try: ctypes.windll.user32.UnhookWindowsHookEx(self._h)
            except Exception: pass
            self._h = None
        self._proc = None


# =============================================================================
#  EX cut-in overlay (full-screen, click-through)
# =============================================================================
class Cutin(QtWidgets.QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.active = False; self.t0 = 0.0; self.dur = 2.0; self.anchor = (0, 0); self.who = ""

    def start(self, geom, ax, ay, dur, who=""):
        self.setGeometry(geom.x(), geom.y(), geom.width(), geom.height() - 1)
        self.anchor = (ax - geom.x(), ay - geom.y()); self.t0 = time.time(); self.dur = dur
        self.who = (who or "").upper()
        self.active = True; self.show()

    def step(self):
        if time.time() - self.t0 > self.dur: self.active = False; self.hide()
        else: self.update()

    def paintEvent(self, _e):
        if not self.active: return
        t = time.time() - self.t0
        W, H = self.width(), self.height()
        a = min(1.0, t / 0.18) * min(1.0, max(0.0, (self.dur - t) / 0.35))
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(4, 14, 40, int(125 * a)))
        ax, ay = self.anchor
        rnd = random.Random(int(t * 24))
        for _ in range(46):                                    # speed lines
            ang = rnd.uniform(0, math.tau); r0 = H * rnd.uniform(0.22, 0.4); r1 = H * rnd.uniform(0.7, 1.3)
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, int(rnd.uniform(40, 120) * a)), rnd.uniform(1, 3)))
            p.drawLine(QPointF(ax + math.cos(ang) * r0, ay + math.sin(ang) * r0),
                       QPointF(ax + math.cos(ang) * r1, ay + math.sin(ang) * r1))
        slide = 1 - (1 - min(1.0, t / 0.30)) ** 3
        p.save(); p.translate(W / 2, max(H * 0.2, min(H * 0.8, ay))); p.rotate(-9)
        bw, bh = W * 1.7, H * 0.17
        x = -bw / 2 + (1 - slide) * (-W * 1.3)
        g = QtGui.QLinearGradient(x, 0, x + bw, 0)
        g.setColorAt(0.0, QtGui.QColor(10, 70, 190, 0)); g.setColorAt(0.15, QtGui.QColor(18, 120, 255, int(225 * a)))
        g.setColorAt(0.75, QtGui.QColor(70, 200, 255, int(225 * a))); g.setColorAt(1.0, QtGui.QColor(70, 200, 255, 0))
        p.fillRect(QRectF(x, -bh / 2, bw, bh), QtGui.QBrush(g))
        for yy, hh in ((-bh / 2 - 7, 4), (bh / 2 + 3, 4)):
            p.fillRect(QRectF(x + bw * 0.1, yy, bw * 0.8, hh), QtGui.QColor(255, 255, 255, int(235 * a)))
        f1 = QtGui.QFont("Arial Black"); f1.setPixelSize(int(H * 0.085)); f1.setItalic(True); f1.setBold(True)
        tx = -W * 0.30 + (1 - slide) * W * 0.6
        path = QtGui.QPainterPath(); path.addText(tx, bh * 0.08, f1, "EX SKILL")
        p.strokePath(path, QtGui.QPen(QtGui.QColor(8, 40, 120, int(255 * a)), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.fillPath(path, QtGui.QColor(255, 255, 255, int(255 * a)))
        f2 = QtGui.QFont("Arial"); f2.setPixelSize(int(H * 0.03)); f2.setBold(True); f2.setItalic(True)
        p.setFont(f2); p.setPen(QtGui.QColor(225, 245, 255, int(255 * a)))
        p.drawText(QPointF(tx + 8, bh * 0.40), self.who or "EX SKILL")
        p.restore()
        if t < 0.14:
            p.fillRect(self.rect(), QtGui.QColor(255, 255, 255, int(200 * (1 - t / 0.14))))
        p.end()


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


class Pet(QtWidgets.QWidget):
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
        self.rig = Rig(ch.model)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.px = self.py = 0.0; self.vx = self.vy = 0.0
        self.W, self.H, self.dpr, self.G = 100, 100, 1.0, 2600.0
        self.grounded = False; self.support = None; self.bounces = 0
        self.state = "idle"; self.state_t = 0.0; self.state_dur = 0.0; self.shot_key = None
        self.anim_key = "idle"; self.air_kind = "jump"
        self.walk_dir = 1; self.edge_decided = False; self.step_off = False
        self.plan_t = 2.0; self.chat_t = random.uniform(25, 60)
        self.dragging = False; self.press_g = None; self.press_win = QPoint(); self.rotating = False
        self.rot_yaw0 = 0.0; self.hist = deque(maxlen=12); self.drag_started_say = 0.0
        self.gx = self.gy = 0.0; self.body_yaw = 0.0; self.head_yaw = self.head_pitch = 0.0
        self.roll = self.roll_v = 0.0; self.sq = self.sq_v = 0.0; self.zoom = 1.0
        self.spin_t = -1.0; self.bow = 0.0; self.last_px = 0.0; self.last_py = 0.0
        # hold-physics drive: smoothed window velocity + 0..1 hold energy
        self.hold_vx = self.hold_vy = 0.0; self.hold_e = 0.0
        self.wind_vx = self.wind_vy = 0.0
        self.pitch_hold = self.pitch_hold_v = 0.0
        self.light = np.array([-0.5, 0.6, 0.8])
        self.head_px = (BASE_W / 2, BASE_H * 0.3)
        self.bubble = None; self.parts = []
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
        self.rag_t = 0.0; self.rag_rest = 0.0; self.rag_hit_t = 0.0
        self.rag_want_up = False          # "Get up" asked for while she was still flying
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
        self.rag_drop = 0.0                              # pelvis -> feet, window px
        self.rag_com = RAG_COM_FRAC               # measured off her own pelvis, see rag_resolve
        self.rag_names = []                # lowercased bone names, see rag_bone
        self.rag_skel = None               # her RagSkeleton while she is limp, else None
        self.last_M = np.eye(4); self.rag_bones = []
        self.apply_flags(show=False)
        self.apply_scale()
        self.play("idle")
        self.rag_resolve()

    # ---------------- ragdoll: bone lookup + centre of mass ----------------
    def rag_resolve(self):
        """Find the limb bones and measure her centre of mass, once, at startup.

        A model whose rig uses other names simply skips those limbs and they
        keep playing their baked animation - the body still tumbles.
        """
        names = [n.lower() for n in self.model.names]
        self.rag_names = names            # cached, rag_bone() hits it every frame
        def find(s):
            for i, n in enumerate(names):
                if n == s or n.endswith(" " + s): return i      # exact token: skips "...Twist" bones
            return None
        self.rag_bones = []
        for b, c, lim, k, damp in RAG_LIMBS:
            bi, ci = find(b), find(c)
            if bi is not None and ci is not None:
                self.rag_bones.append(dict(b=bi, c=ci, lim=lim, k=k, damp=damp, d=0.0, dd=0.0))
        # Where the rod's centre of mass has to sit so that the point she actually
        # rotates about (the pelvis, used by render_frame) sits ON the surface once
        # she is lying flat. Her pelvis rides at a different fraction of her height
        # in every rig, so measure it in the limp base pose instead of guessing:
        #   pivot_y = COM + drop - pelvis_frac*ch, and the rod resting on the floor
        #   means COM + thickness == ground, so COM = pelvis_frac + thickness/ch.
        self.rag_com = RAG_COM_FRAC
        try:
            c = self.clips.get("pickup") or self.clips.get("idle")
            if c is not None:
                W = self.model.pose_world(c.T[-1], c.Q[-1], c.S[-1])
                pts = self.model.posed_positions(W, skip_fx=True)
                if len(pts):
                    y = pts[:, 1]
                    lo, hi = float(np.percentile(y, 0.2)), float(np.percentile(y, 99.8))
                    frac = (float(W[self.model.pelvis][1, 3]) - lo) / max(1e-6, hi - lo)
                    self.rag_com = clamp(frac, 0.15, 0.75) + RAG_COM_PAD / Renderer.FRAC
        except Exception as e:
            print(f"[warn] {self.name}: could not measure the ragdoll centre of mass: {e}")
        print(f"[ragdoll] {self.name}: {len(self.rag_bones)}/{len(RAG_LIMBS)} limbs found, "
              f"COM {self.rag_com * 100:.1f}% up")

    # ---------------- ragdoll: the articulated skeleton (pymunk) ----------------
    # The body table lives in ragphys.py's RAGP_* block; this is just the bones.
    # (origin bone, end bone, radius x her height, mass weight, joint limit)
    RAGP_LINKS = [
        ("pelvis", "chest", 0.045, 0.20, ragphys.RAGP_TORSO_LIM),
        ("chest", "neck", 0.050, 0.30, ragphys.RAGP_TORSO_LIM),
        ("neck", "head", 0.060, 0.07, ragphys.RAGP_HEAD_LIM),
        ("l upperarm", "l forearm", 0.018, 0.03, 2.4),
        ("l forearm", "l hand", 0.016, 0.025, 1.6),
        ("r upperarm", "r forearm", 0.018, 0.03, 2.4),
        ("r forearm", "r hand", 0.016, 0.025, 1.6),
        ("l thigh", "l calf", 0.028, 0.09, 1.5),
        ("l calf", "l foot", 0.022, 0.06, 1.2),
        ("r thigh", "r calf", 0.028, 0.09, 1.5),
        ("r calf", "r foot", 0.022, 0.06, 1.2),
    ]

    def rag_bone(self, token):
        """Rig node index for a bone name, or None. Same matching as rag_resolve:
        an exact token, so "...Twist" bones never match."""
        names = self.rag_names
        for i, n in enumerate(names):
            if n == token or n.endswith(" " + token):
                return i
        return None

    def rag_project(self, node):
        """Bone `node` in WINDOW px, y down - the same maths as rag_limb_post."""
        p = self.last_M @ np.append(self.rig.world_base[node][:3, 3], 1.0)
        wpp = self.ch.renderer.world_per_px * max(1e-6, self.dpr)
        return (float(p[0]) / wpp, -float(p[1]) / wpp)

    def rag_spec(self):
        """Build the pymunk skeleton spec from the CURRENT frame, or None.

        Built here, at ragdoll start, rather than once at startup: that way
        face_yaw, the cfg yaw_offset and her scale are all already baked into
        the pose being measured, so no extra transform has to be reconstructed
        later. Yaw in particular changes which way a limb points on screen.

        Returns None when her rig cannot support it - no pelvis, no thighs, no
        head, or fewer than a handful of bodies - and the caller falls back to
        the legacy rod for this pet.
        """
        if ragphys is None or self.last_M is None or not np.all(np.isfinite(self.last_M)):
            return None
        pel = self.rag_bone("pelvis")
        if pel is None or not self.rag_bone("l thigh") or not self.rag_bone("neck"):
            return None
        ch = self.H * Renderer.FRAC                      # her height, window px
        # A rig with no chest bone (most of them - the torso is Spine + Spine1)
        # gets ONE torso body spanning pelvis -> neck instead of two, per the
        # body table. Without this every one of her links that ran through the
        # chest would be dropped and she would have no root at all.
        table = list(self.RAGP_LINKS)
        if self.rag_bone("chest") is None:
            table = [(a, b, r, w, l) for a, b, r, w, l in table if a != "chest"]
            table.insert(0, ("pelvis", "neck", 0.050, 0.30, ragphys.RAGP_TORSO_LIM))
        links, idx = [], {}
        for a, b, rad, w, lim in table:
            ia, ib = self.rag_bone(a), self.rag_bone(b)
            if ia is None or ib is None:
                continue
            idx[a] = len(links)
            links.append((a, b, ia, ib, rad, w, lim))
        if len(links) < ragphys.RAGP_MIN_BODIES or "pelvis" not in idx:
            return None
        # every parent has to exist and come earlier in the list
        parent = {}
        for a, b, _ia, _ib, _r, _w, _l in links:
            p = self.rag_parent_of(a)
            parent[a] = None if p is None else idx.get(p, -1)
        bodies = []
        for a, b, ia, ib, rad, w, lim in links:
            k, damp = self.rag_spring(a)
            bodies.append(dict(
                name=a, bone=ia, parent=parent[a],
                p=self.rag_project(ia), c=self.rag_project(ib),
                r=rad * ch, w=w, lim=lim, k=k, damp=damp,
                head=(a == "neck")))                    # the skull rides on the neck body
        # Where her feet are, below the pelvis, in window px: this is what the
        # physics pelvis position is converted back into (window x, window y).
        feet = max((self.rag_project(self.rag_bone(t))[1]
                    for t in ("l foot", "r foot") if self.rag_bone(t) is not None),
                   default=self.rag_project(pel)[1])
        return dict(scale=self.cfg["scale"], px=self.px, py=self.py,
                    root=idx["pelvis"], drop=feet - self.rag_project(pel)[1], bodies=bodies)

    def rag_parent_of(self, bone):
        """The physics body `bone` hangs off: its nearest ancestor that has one.

        Walks the real rig hierarchy rather than assuming a shape, because the
        chain differs per model. Mika for instance has no chest bone and the arms
        reach the torso through Clavicle -> Spine1 -> Spine, so an upperarm's
        nearest ancestor with a body is the pelvis (the single torso body), while
        a forearm's is the upperarm - which is exactly what chains the arm into
        an elbow instead of pinning both segments to her spine.
        """
        m = self.model
        n = self.rag_bone(bone)
        have = {t for t in ("pelvis", "chest") if self.rag_bone(t) is not None}
        # bodies are named by their ORIGIN bone (the first column), because that
        # is the joint they hang from and the bone their post rotation turns
        have |= {a for a, _b, _r, _w, _l in self.RAGP_LINKS}
        cur = int(m.parent[n]) if n is not None else -1
        while cur >= 0:
            nm = m.names[cur].lower()
            for tok in have:
                if nm == tok or nm.endswith(" " + tok):
                    return tok
            cur = int(m.parent[cur])
        return None

    def rag_spring(self, bone):
        """(stiffness, damping) for a link, reusing the RAG_LIMBS numbers."""
        for b, _c, lim, k, damp in RAG_LIMBS:
            if b == bone:
                return k, damp
        if bone in ("pelvis", "chest"):
            return ragphys.RAGP_TORSO_K, ragphys.RAGP_TORSO_DAMP
        if bone == "neck":
            return ragphys.RAGP_HEAD_K, ragphys.RAGP_HEAD_DAMP
        return ragphys.RAGP_TORSO_K, ragphys.RAGP_TORSO_DAMP

    # ---------------- geometry helpers ----------------
    def feet_pos(self): return self.px + self.W / 2, self.py + self.H * (1 - self.PADB)

    def set_feet(self, fx, fy):
        self.px = fx - self.W / 2; self.py = fy - self.H * (1 - self.PADB)
        self.move(int(round(self.px)), int(round(self.py)))

    def apply_flags(self, show=True):
        f = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.cfg["always_on_top"]: f |= Qt.WindowType.WindowStaysOnTopHint
        vis = self.isVisible()
        self.setWindowFlags(f)
        self._pt = False        # recreating the window resets the ex-style
        if vis or show: self.show()

    def set_passthrough(self, on):
        """A3: on Windows let the window manager do hit-testing per pixel instead
        of an every-3rd-frame mask region, which clipped fast limbs and swallowed
        the heart / star / puff particles."""
        if sys.platform != "win32" or on == self._pt or not self.isVisible(): return
        self._pt = on
        try:
            u = ctypes.windll.user32
            u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]; u.GetWindowLongW.restype = ctypes.c_long
            u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
            hwnd = int(self.winId())
            st = u.GetWindowLongW(hwnd, -20)                             # GWL_EXSTYLE
            u.SetWindowLongW(hwnd, -20, (st | 0x80020) if on else (st & ~0x20))
        except Exception: pass

    def apply_scale(self):
        s = self.cfg["scale"]; fx, fy = self.feet_pos()
        self.W, self.H = int(BASE_W * s), int(BASE_H * s)
        scr = self.screen()
        self.dpr = scr.devicePixelRatio() if scr else 1.0
        self.G = 2600.0 * max(0.6, s)
        self.bfont.setPointSizeF(max(8.0, 9.5 * math.sqrt(s)))
        self.setFixedSize(self.W, self.H)
        self.ch.renderer.set_size(max(32, int(round(self.W * self.dpr))), max(32, int(round(self.H * self.dpr))))
        self.set_feet(fx, fy); self.mask_dirty = True

    # ---------------- dialogue ----------------
    def line(self, *events):
        """One line for `event` ("poke", "idle", ...), or drawn from several.

        The pools themselves live in the dialogue package; this only asks for one
        line and fills in who is speaking.
        """
        tags = self.A.watcher.current_tags()
        if len(events) == 1:
            return self.A.dialogue.get(self.name, events[0], tags)
        return self.A.dialogue.get_any(self.name, events, tags)

    def spawn(self, index=0):
        g = self.A.resolve_screen()
        saved = self.cfg["positions"].get(self.name) if isinstance(self.cfg.get("positions"), dict) else None
        if saved is None and index == 0:
            saved = self.cfg["pos_x"]
        if saved is None:                                       # spread them out on the one screen
            n = max(1, len(self.A.pets))
            frac = 0.9 - index * min(0.75 / n, 0.25)
            saved = g.left() + (g.right() - g.left()) * frac
        fx = self.A.clamp_x(saved, self.W)
        self.rag_clear()
        self.set_feet(fx, self.A.floor_y(fx)); self.grounded = True
        self.show(); self.do_tactical(first=True)

    # ---------------- animation / state ----------------
    def play(self, key, restart=True):
        name, loop = ANIM_KEYS[key]
        self.anim_key = key
        self.rig.play(self.clips.get(key), loop, 1.0, 0.08 if key == "pickup" else 0.16, restart)

    def enter(self, state, key=None, dur=0.0):
        prev_key = self.anim_key
        self.state, self.state_t, self.state_dur = state, 0.0, dur
        if state == "shot": self.shot_key = key; k = key
        elif state == "drag": k = "pickup"
        elif state == "fall": k = "pickup"   # jumps and throws share the pickup anim
        else: k = state
        self.play(k, restart=(state not in self.LOOPS or k != prev_key))

    def plan_next(self): self.plan_t = random.uniform(2.5, 7.0)

    def speech_ok(self):
        """True when she can start a new line right now.

        Read by StateReactor, which otherwise has to know nothing about the pet
        code. False while she is mid-bubble, off screen or lying limp, so a
        reaction does not cut off a line she is already saying or stand her
        back up mid-tumble.
        """
        return bool(self.cfg["speech"]) and self.bubble is None and self.isVisible() \
            and not self.ragdoll

    def say(self, text, dur=None):
        if not self.cfg["speech"] or not text: return
        self.bubble = (text, time.time(), dur or (2.4 + 0.05 * len(text))); self.mask_dirty = True
        self.lift_bubble()

    def lift_bubble(self):
        """Put this character's window - and therefore her bubble - on top.

        Every character draws her own bubble inside her own window, so when two of
        them overlap it is the *window* z-order that decides which line is readable.
        Raising the window as soon as a line starts makes the newest speech the one
        on top, and it stays there until somebody else talks.
        """
        if self.dragging or not self.isVisible(): return
        try:
            self.raise_()
            self.A.touch_zorder(self)
        except Exception:
            pass

    def emit(self, kind, n, x=None, y=None, spread=70):
        if not self.cfg["particles"]: return
        if x is None: x, y = self.head_px
        sc = self.cfg["scale"]
        for _ in range(n):
            ang = random.uniform(-math.pi * 0.95, -math.pi * 0.05); sp = random.uniform(40, 170) * sc
            self.parts.append(dict(kind=kind, x=x + random.uniform(-spread, spread) * 0.5 * sc, y=y + random.uniform(-10, 10),
                                   vx=math.cos(ang) * sp, vy=math.sin(ang) * sp, life=0.0, max=random.uniform(0.9, 1.6),
                                   size=random.uniform(7, 13) * math.sqrt(sc), rot=random.uniform(-0.5, 0.5)))

    def emit_wind(self, cx, cy, strength):
        """Wind streaks flowing straight away from the gust origin (screen px),
        using the same direction and falloff the hair uses in gust_wind_at()."""
        if not self.cfg["particles"]: return
        sc = self.cfg["scale"]
        for _ in range(int(22 * clamp(strength, 0.3, 2.5))):
            lx, ly = random.uniform(0, self.W), random.uniform(0, self.H)   # spot in this window
            dx, dy = self.px + lx - cx, self.py + ly - cy                   # same vector the hair sees
            d = math.hypot(dx, dy)
            if d > GUST_RADIUS or d < 20: continue
            ux, uy = dx / d, dy / d
            spd = GUST_SPEED * 0.35 * strength * (1 - d / GUST_RADIUS) ** 0.5
            self.parts.append(dict(
                kind="wind", x=lx, y=ly, vx=ux * spd, vy=uy * spd,
                life=-d / (GUST_SPEED * 0.5),            # negative = waits, so a wavefront sweeps outward
                max=random.uniform(0.35, 0.6),
                size=random.uniform(1.2, 2.4) * math.sqrt(sc), rot=0.0,
                ang=math.atan2(uy, ux), len=clamp(spd * 0.08, 14, 90) * math.sqrt(sc),
                ph=random.uniform(0, math.tau)))

    # ---- pet-to-pet ----
    # All of the conversation logic lives in dialogue/conversation.py. Only the
    # entry points this class and the panel still use are kept here.
    def peers(self, max_dist=None, same_surface=False):
        """Other visible pets, nearest first. same_surface = perched on the same window."""
        return self.A.peers_of(self, max_dist, same_surface)

    def free(self):
        """True when she can start / continue a conversation."""
        return self.conv.free()

    def in_chat(self, now=None):
        return self.conv.in_chat(now)

    def busy_chat(self):
        """True while a conversation is running (or about to) - blocks wander plans."""
        return self.conv.busy()

    def greet(self):
        """One-line hello to whoever is nearest - used when two characters pass."""
        return self.conv.greet()

    def greet_near(self, other):
        return self.conv.greet_near(other)

    def start_chat(self, other):
        """Begin a conversation with `other` immediately (no approach step)."""
        return self.conv.start(other)

    def chat_abort(self, why=""):
        """Something else happened (drag / poke / cut-in) - drop out of the chat."""
        return self.conv.abort(why)

    def chat_stop(self):
        """User-driven stop (menu / panel)."""
        self.conv.stop()

    def chat_logic(self, dt, now):
        """Continue an ongoing conversation, or let one start."""
        return self.conv.converse(dt, now)

    def audience(self, verb="poke"):
        """A nearby pet comments on what the user just did to this one."""
        return self.conv.audience(verb)

    # ---- actions (used by menu / panel / mouse) ----
    def poke(self):
        if self.ragdoll: self.rag_shove(); return
        if not self.grounded or self.dragging: return
        self.enter("shot", "react"); self.say(self.line("poke")); self.emit("star", 7)
        self.sq_v += 1.2
        self.audience("poke")

    def pet(self):
        if not self.grounded or self.dragging: return
        self.enter("shot", "react"); self.say(self.line("petting")); self.emit("heart", 9); self.bow = 1.0
        self.audience("petting")

    def do_cutin(self):
        if not self.grounded or self.dragging: return
        self.chat_abort()
        self.enter("shot", "cutin"); self.say(self.line("cutin"), 3.0)
        scr = self.screen().geometry() if self.screen() else QtWidgets.QApplication.primaryScreen().geometry()
        c = self.clips.get("cutin"); dur = clamp(c.duration if c else 2.0, 1.8, 4.0)
        self.A.cutin.start(scr, self.px + self.head_px[0], self.py + self.head_px[1], dur, self.name)
        self.raise_(); self.emit("star", 14, spread=160)

    def do_tactical(self, first=False):
        if not self.grounded or self.dragging: return
        self.enter("shot", "tactical"); self.say(self.line("tactical_first" if first else "tactical"), 3.6)

    def spin(self): self.spin_t = 0.0

    def launch(self):
        if self.ragdoll: self.rag_shove(2.0); return
        self.ragdoll = False; self.rig.rag_post = []; self.rig.dangle_on = True
        self.grounded = False; self.support = None; self.dragging = False
        self.vx = random.choice([-1, 1]) * random.uniform(500, 1400) * self.cfg["scale"]
        self.vy = -random.uniform(1500, 2300) * self.cfg["scale"]
        self.air_kind = "thrown"; self.bounces = 0; self.enter("fall"); self.say(self.line("launch"), 1.8)

    def recall(self):
        """Called when the user summons this specific character."""
        self.say(self.line("summon"), 2.0)

    def play_anim(self, key):
        if self.dragging or not self.grounded: return
        if key == "cutin": self.do_cutin()
        elif key == "idle": self.enter("idle"); self.plan_t = 8
        elif key == "walk": self.walk_dir = random.choice([-1, 1]); self.enter("walk", dur=5.0)
        elif key == "alert": self.enter("alert", dur=6.0)
        else: self.enter("shot", key)

    def hideEvent(self, e):
        """A hidden pet must not stay in the physics world.

        Nothing ticks a hidden window, so a body left behind would sit there
        colliding with the other pets and the windows forever.
        """
        self.rag_clear()
        super().hideEvent(e)

    def rag_clear(self):
        """Snap out of the ragdoll without moving her. Call before any teleport.

        Always removes her skeleton from the physics world, whichever engine she
        was on: a body left behind in the space would keep colliding with the
        other pets and the windows after she has stood back up.
        """
        if self.rag_skel is not None and self.A.rag_world is not None:
            self.A.rag_world.remove_skeleton(self.rag_skel)
        self.rag_skel = None
        self.ragdoll = False; self.rag_state = "fall"; self.rag_want_up = False
        self.rig.dangle_on = True; self.rig.rag_post = []
        # She is standing again, so the rig animates once more. The clip is NOT
        # chosen here: callers that want a specific one (rag_end's "react", a
        # recall's "idle") call enter() right after this, and Rig.play refuses to
        # switch while the rig is still marked limp. Order matters - clear the
        # flag first, then let them pick.
        self.rig.set_limp(False)
        for L in self.rag_bones: L["d"] = L["dd"] = 0.0
        self.rg_contact = False; self.rg_grav = 1.0; self.rag_strength = 1.0
        self.rg_ax = self.rg_ay = self.rg_alpha = 0.0; self.rg_top = None

    def reset_position(self):
        g = self.A.resolve_screen()
        fx = self.A.clamp_x(g.right() - self.W * 0.9, self.W)
        self.rag_clear()
        self.support = None; self.grounded = True; self.dragging = False; self.vx = self.vy = 0
        self.set_feet(fx, self.A.floor_y(fx)); self.enter("idle")

    def try_jump(self):
        fx, fy = self.feet_pos(); s = self.cfg["scale"]; cand = []
        gh = self.A.resolve_screen()
        for (h, l, t, r, b) in self.A.tracker.wins:
            if r < gh.left() or l > gh.right(): continue          # window lives on another monitor
            hg = fy - t
            if h != self.support and hg > 30 and r - l > 140:
                tx = min(max(fx, l + 60), r - 60)
                tx = self.A.clamp_x(tx, self.W)
                if abs(tx - fx) < 520 * s: cand.append((t, h, tx))
        if not cand: return False
        t, h, tx = random.choice(cand); hg = fy - t
        vy = -math.sqrt(2 * self.G * (hg + 70 * s)); tt = (-vy + math.sqrt(vy * vy - 2 * self.G * hg)) / self.G
        self.vx, self.vy = (tx - fx) / tt, vy
        self.walk_dir = 1 if tx >= fx else -1
        self.grounded = False; self.support = None; self.air_kind = "jump"; self.bounces = 0
        self.enter("fall"); self.say(self.line("jump"), 1.6)
        return True

    # ---------------- ragdoll: start / shove / get up ----------------
    def rag_freeze_pose(self):
        """Hold Formation_Pickup on its last frame: the limp base pose.

        Frozen rather than left playing, for two reasons - the centre of mass
        measured in rag_resolve() comes from exactly this frame, and a limp body
        should not keep animating underneath the physics that is flopping it.

        The rig is put into the full limp state (see Rig.set_limp), not just
        paused, so that nothing can restart the clip: nudging a ragdolled pet
        used to look like it played an animation.
        """
        c = self.clips.get("pickup") or self.clips.get("idle")
        if c is None:
            self.rig.set_limp(True); return
        if not self.rig.limp:
            self.rig.play(c, False, speed=0.0, fade=0.08)
        self.rig.set_limp(True)

    def rag_geom(self):
        """(character height px, body thickness, COM->feet, half-rod length).

        The rod is head end <-> foot end and `rad` thick, so its lowest point
        when upright is exactly COM + a + rad == the feet. Lying flat it rests
        on COM + rad, i.e. she lies ON the surface instead of through it, and
        `self.rag_com` (measured from her pelvis) puts her rotation pivot on it.
        """
        ch = self.H * Renderer.FRAC                  # her height in window px
        rad = RAG_COM_PAD * self.H                   # body "thickness"
        drop = self.rag_com * ch                     # COM -> feet
        return ch, rad, drop, max(1.0, drop - rad)   # ch, rad, drop, half-rod length a

    def start_ragdoll(self, kick=True):
        """Go limp: tumble, bounce, flop, lie there, then get back up.

        With cfg["ragdoll_engine"] == "pymunk" (and pymunk importable) she goes
        into the articulated world instead; the rod below is the fallback, and
        is also what "legacy" selects explicitly.
        """
        if self.ragdoll or not self.isVisible() or self.dragging: return
        self.chat_abort(); self.meet_target = None
        if self.cfg.get("ragdoll_engine", "pymunk") == "pymunk":
            if self.start_ragdoll_pm(kick):
                return
            # no usable skeleton for this rig: say so once and fall back below
        ch, rad, drop, a = self.rag_geom(); s = max(0.6, self.cfg["scale"])
        fx, fy = self.feet_pos()
        self.rg_x, self.rg_y = fx, fy - drop
        self.rg_vx, self.rg_vy = (0.0, 0.0) if self.grounded else (self.vx, self.vy)
        self.rg_a = random.uniform(-0.15, 0.15)       # NEVER exactly 0: a rod balances on its end
        self.rg_w = 0.0
        if kick:
            self.rg_vx += random.choice([-1, 1]) * random.uniform(350, 900) * s
            self.rg_vy -= random.uniform(600, 1200) * s
            self.rg_w = random.choice([-1, 1]) * random.uniform(4, 10)
        self.rg_ax = self.rg_ay = self.rg_alpha = 0.0
        self.rg_prev_low = fy; self.rag_contact = False; self.rag_top = None
        self.rg_grav = 1.0; self.rag_strength = 1.0
        self.rag_state, self.rag_t, self.rag_rest = "fall", 0.0, 0.0
        self.rag_want_up = False
        self.rag_hit_t = 0.0
        for L in self.rag_bones: L["d"] = L["dd"] = 0.0
        self.ragdoll = True; self.rig.dangle_on = False
        self.grounded = False; self.support = None; self.dragging = False
        self.air_kind = "thrown"; self.bounces = 0
        self.roll = self.roll_v = 0.0; self.pitch_hold = self.pitch_hold_v = 0.0
        self.enter("fall"); self.rag_freeze_pose()
        self.say(self.line("launch"), 1.6)

    def start_ragdoll_pm(self, kick=True):
        """Hand her to the pymunk world. True if she went in, False to fall back.

        The skeleton is built from the frame on screen right now, so her current
        pose, scale and facing are what the physics starts from.
        """
        W = self.A.rag_world
        if W is None or self.rag_pm_failed:
            return False
        try:
            # The spec is measured off the frozen limp pose, so freeze first and
            # let last_M catch up: she must go limp from where she is standing.
            self.enter("fall"); self.rag_freeze_pose()
            self.rig.advance(0.0)
            self.render_frame(QtGui.QCursor.pos())
            spec = self.rag_spec()
        except Exception as e:
            print(f"[ragdoll] {self.name}: skeleton build failed ({e}); using the rod ragdoll")
            self.rag_pm_failed = True
            return False
        if spec is None:
            print(f"[ragdoll] {self.name}: rig has no usable ragdoll bones; using the rod ragdoll")
            self.rag_pm_failed = True
            return False
        s = max(0.6, self.cfg["scale"])
        vx, vy = (0.0, 0.0) if self.grounded else (self.vx, self.vy)
        w = 0.0
        if kick:
            vx += random.choice([-1, 1]) * random.uniform(*ragphys.RAGP_KICK_VX) * s
            vy -= random.uniform(*ragphys.RAGP_KICK_VY) * s
            w = random.choice([-1, 1]) * random.uniform(*ragphys.RAGP_KICK_W)
        try:
            # BEFORE add_skeleton: ragphys reads RAGP_SELF_COLLIDE while it is
            # building the shapes and filling in the ancestor mute set, so setting
            # it afterwards would leave this ragdoll with the group filter and no
            # self-collision despite the cfg saying otherwise.
            ragphys.RAGP_SELF_COLLIDE = bool(self.cfg.get("ragdoll_self_collide", False))
            self.rag_skel = W.add_skeleton(self.rag_owner, spec, (vx, vy, w))
        except Exception as e:
            print(f"[ragdoll] {self.name}: could not build the physics skeleton ({e}); "
                  "using the rod ragdoll")
            self.rag_pm_failed = True
            return False
        self.ragdoll = True; self.rig.dangle_on = False
        # --rag-trace: per-frame physics for the first N frames, which is where
        # an instant launch is actually visible - a big anchor or relative-angle
        # error, or a pelvis nowhere near where the window says it is.
        if getattr(self.A, "rag_trace", 0):
            anch, rel = self.rag_skel.joint_error()
            print(f"[rag-trace] {self.name}: start pelvis="
                  f"({self.rag_skel.bodies[self.rag_skel.root].position[0]:.2f},"
                  f"{self.rag_skel.bodies[self.rag_skel.root].position[1]:.2f}) "
                  f"window={self.px:.0f},{self.py:.0f} drop={self.rag_skel.drop:.2f} "
                  f"bodies={len(self.rag_skel.bodies)} anchor_err={anch:.3f}px "
                  f"rel_err={rel:.4f}rad ignore={len(W._ignore)}")
            W.trace(int(self.A.rag_trace))
        self.grounded = False; self.support = None; self.dragging = False
        self.air_kind = "thrown"; self.bounces = 0
        self.roll = self.roll_v = 0.0; self.pitch_hold = self.pitch_hold_v = 0.0
        self.rag_state, self.rag_t, self.rag_rest = "fall", 0.0, 0.0
        self.rag_want_up = False; self.rag_hit_t = 0.0
        self.rg_ax = self.rg_ay = self.rg_alpha = 0.0
        self.rg_contact = False; self.rg_top = None
        self.rg_grav = 1.0; self.rag_strength = 1.0
        for L in self.rag_bones: L["d"] = L["dd"] = 0.0
        self.say(self.line("launch"), 1.6)
        return True

    def rag_shove(self, power=1.0):
        """Click / Launch on a ragdolled pet = push her away from the cursor."""
        if not self.ragdoll: return
        s = max(0.6, self.cfg["scale"]); cur = QtGui.QCursor.pos()
        self.rag_state = "fall"; self.rag_rest = 0.0; self.rag_strength = 1.0
        if self.rag_skel is not None:
            # Into the physics body nearest her, not into her centre of mass: the
            # shove should visibly spin her, not just translate her.
            self.rag_skel.shove((cur.x(), cur.y()), power)
            self.say(self.line("poke"), 1.4)
            return
        self.rg_vx += clamp((self.rg_x - cur.x()) * 6, -1400, 1400) * power
        self.rg_vy -= random.uniform(500, 900) * s * power
        self.rg_w += random.uniform(-8, 8) * power
        self.say(self.line("poke"), 1.4)

    def rag_getup(self):
        if not self.ragdoll or self.rag_state == "getup": return
        # Asked to get up while she is still flying: remember it and stand her up
        # the moment she lands, rather than snapping her down to the floor.
        s = max(0.6, self.cfg["scale"])
        if not self.rag_contact and abs(self.rg_vy) > 260.0 * s:
            self.rag_want_up = True
            return
        self.rag_want_up = False
        self.rag_state, self.rag_t = "getup", 0.0
        if self.rag_skel is not None:
            # Record the pose she is lying in and take her OUT of the world. The
            # blend below plays it back into the rig; while it runs nothing can
            # hit her, which is acceptable for well under a second.
            pose = self.rag_skel.pose()
            self.rag_phi0 = list(pose["phi"])
            # the skeleton goes away, so keep what the post list needs
            self.rag_pm_bones = dict(bones=list(self.rag_skel.bones),
                                     parents=list(self.rag_skel.parents))
            self.rag_a0 = -pose["phi"][self.rag_skel.root]   # rg_a = +phi_pelvis
            self.rg_x0, self.rg_y0 = pose["x"], pose["y"]
            self.rag_drop = self.rag_skel.drop
            self.A.rag_world.remove_skeleton(self.rag_skel)
            self.rag_skel = None
            self.rag_phi = list(pose["phi"])          # the get-up blend reads this
            return
        self.rg_a0 = (self.rg_a + math.pi) % math.tau - math.pi    # shortest way back to upright
        self.rg_x0, self.rg_y0 = self.rg_x, self.rg_y
        self.rg_vx = self.rg_vy = self.rg_w = 0.0

    def rag_end(self, surf):
        self.rag_clear()
        self.grounded = True; self.support = surf; self.vx = self.vy = 0.0; self.bounces = 0
        self.enter("shot", "react"); self.plan_next()
        self.say(self.line("land"), 1.8)

    # ---------------- ragdoll: body (one rigid rod) ----------------
    def rag_ground(self, x, y_ref):
        """Top-most surface still at or below y_ref (windows are one-way platforms).

        The surface she is already resting on is pinned back in: without that,
        landing on a window title bar leaves the rod's low end below the bar, and
        the one-way test would then drop her straight through it on the next frame.
        """
        if self.rg_top is not None: y_ref = min(y_ref, self.rg_top + 8.0)
        for top, h in self.A.surfaces(x, self.cfg["perch"]):       # sorted top -> bottom
            if top >= y_ref - 6: return top, h
        return self.A.floor_y(x), None

    def ragdoll_step(self, dt):
        dt = min(dt, 1 / 30); s = max(0.6, self.cfg["scale"]); G = self.G
        ch, rad, drop, a = self.rag_geom(); gh = self.A.resolve_screen()
        self.rag_t += dt; self.rag_hit_t += dt
        if self.rag_skel is not None:
            self.ragdoll_step_pm(dt); return
        v0x, v0y, w0 = self.rg_vx, self.rg_vy, self.rg_w

        if self.rag_state == "getup":
            k = min(1.0, self.rag_t / RAG_GETUP_TIME); sm = k * k * (3 - 2 * k)
            ground, surf = self.rag_ground(self.rg_x, self.rg_y0 + a + rad)
            self.rg_a = self.rg_a0 * (1 - sm)
            self.rg_y = self.rg_y0 + (ground - a - rad - self.rg_y0) * sm
            self.rag_strength = 1 - sm
            self.rg_vx = self.rg_vy = self.rg_w = 0.0
            self.rg_top = ground
            self.set_feet(self.rg_x, self.rg_y + drop)
            if k >= 1.0: self.rag_end(surf)
            self.rag_accel(dt, v0x, v0y, w0); return

        # ---- integrate ----
        self.rg_vy += G * dt
        self.rg_x += self.rg_vx * dt; self.rg_y += self.rg_vy * dt; self.rg_a += self.rg_w * dt
        self.rg_w *= math.exp(-0.15 * dt)
        lo, hi = gh.left() + self.W * 0.15, max(gh.left() + self.W * 0.15, gh.right() - self.W * 0.15)
        if self.rg_x < lo:   self.rg_x = lo; self.rg_vx = abs(self.rg_vx) * 0.5;  self.rg_w *= 0.7
        elif self.rg_x > hi: self.rg_x = hi; self.rg_vx = -abs(self.rg_vx) * 0.5; self.rg_w *= 0.7
        ceil = gh.top() + a
        if self.rg_y < ceil: self.rg_y = ceil; self.rg_vy = abs(self.rg_vy) * 0.4

        # ---- contacts: rod ends (+ thickness) against the ground ----
        # Both ends stay active inside a small speculative margin (about a frame of
        # travel). Correcting penetration on its own drops the other end out of
        # contact every pass, and a rod lying flat then rocks itself forever instead
        # of settling - which would also mean she never lies still long enough to
        # get up. With the margin the sequential impulses actually converge.
        m, I = 1.0, 0.5 * a * a                       # fat rod: spins less than a thin one
        ground, surf = self.rag_ground(self.rg_x, self.rg_prev_low)
        margin = 2.0 * s + abs(self.rg_vy) * dt
        self.rag_contact = False; impact = 0.0
        for _ in range(6):
            for sgn in (1, -1):
                th = self.rg_a
                rx = sgn * a * math.sin(th); ry = -sgn * a * math.cos(th) + rad     # COM -> contact point
                pen = (self.rg_y + ry) - ground
                if pen < -margin: continue                 # nowhere near the surface
                if pen > 0.0:
                    self.rg_y -= pen                      # push her out
                    self.rag_contact = True
                vcx = self.rg_vx - self.rg_w * ry           # velocity of the contact point
                vcy = self.rg_vy + self.rg_w * rx
                if vcy <= 0: continue                       # already separating
                self.rag_contact = True
                e = RAG_REST_E if vcy > 500 * s else 0.0    # only real hits bounce
                j = (1 + e) * vcy / (1 / m + rx * rx / I)
                self.rg_vy -= j / m;  self.rg_w -= j * rx / I
                jt = clamp(-vcx / (1 / m + ry * ry / I), -RAG_FRICTION * j, RAG_FRICTION * j)
                self.rg_vx += jt / m; self.rg_w -= ry * jt / I
                impact = max(impact, vcy)
        th = self.rg_a
        self.rg_top = ground
        # Her rod is a capsule, so its lowest point is the lowest axis end plus the
        # thickness. Using exactly that as the one-way reference is what makes a
        # window title bar behave the way air() already does: she can land on it and
        # stay, and she can still drop through it from above.
        self.rg_prev_low = self.rg_y + rad + a * abs(math.cos(th))

        speed = math.hypot(self.rg_vx, self.rg_vy) + abs(self.rg_w) * a
        if self.rag_contact:
            self.rg_w *= math.exp(-3.0 * dt)                # lying on the floor: bleed spin
            self.rg_vx *= math.exp(-1.2 * dt)
            # Once the normal speed is a nudge rather than a hit, take the last of
            # the rocking out by hand: a pixel or two of solver jitter on a perfectly
            # flat floor would otherwise keep her from ever lying still.
            if abs(self.rg_vy) < 0.4 * 500 * s and abs(self.rg_w) < 1.0:
                self.rg_vy = 0.0
                self.rg_w *= math.exp(-9.0 * dt)
            if abs(math.sin(self.rg_a)) < 0.35:             # standing on her feet = unstable, tip her
                self.rg_w += (1 if math.sin(self.rg_a) >= 0 else -1) * 6 * dt
            # A rod stays balanced on one end only while something keeps disturbing
            # it, and nothing does once she has stopped moving - so ease whatever
            # tilt is left out of "lying flat". Modulo pi: which way she flops is free.
            if speed < 260.0 * s:
                d = (self.rg_a - 0.5 * math.pi) % math.pi
                if d > 0.5 * math.pi: d -= math.pi
                self.rg_a -= d * min(1.0, 4.0 * dt)
        if impact > 700 * s and self.rag_hit_t > 0.25:
            self.rag_hit_t = 0.0
            self.sq = min(0.26, impact / 6500)
            self.emit("puff", 5, self.W / 2, self.H * (1 - self.PADB), spread=90)

        # ---- rest detection -> auto get-up ----
        if self.rag_contact and speed < 40 * s: self.rag_rest += dt
        else: self.rag_rest = 0.0
        if self.rag_want_up and self.rag_rest > 0.2: self.rag_getup()
        elif self.cfg.get("ragdoll_auto_getup", True) and self.rag_rest > RAG_REST_TIME:
            self.rag_getup()

        # ---- sync window + legacy fields (hair/cloth springs read vx/vy) ----
        self.vx, self.vy = self.rg_vx, self.rg_vy
        self.set_feet(self.rg_x, self.rg_y + drop)
        self.rag_accel(dt, v0x, v0y, w0)
        if not all(map(math.isfinite, (self.rg_x, self.rg_y, self.rg_a))):   # failsafe: never get stuck
            self.rag_clear(); self.reset_position()

    # ---------------- ragdoll: the articulated body (pymunk) ----------------
    def ragdoll_step_pm(self, dt):
        """Read the physics pose and drive everything else off it.

        The world was stepped once for the whole app (App.tick), so this only
        reads. Three things come out of it: where her window goes, which bones
        get a post rotation this frame, and whether she is at rest yet.
        """
        sk = self.rag_skel
        if sk is None or sk.detached:
            self.ragdoll = False; return
        s = max(0.6, self.cfg["scale"])
        v0x, v0y, w0 = self.rg_vx, self.rg_vy, self.rg_w
        try:
            self.rag_pm_failsafe(sk)
            if not self.ragdoll:
                return                              # failsafe already cleaned up
            if self.rag_state == "getup":
                self.rag_getup_step_pm(dt)
                return
            pose = sk.pose()
            if not all(map(math.isfinite, (pose["x"], pose["y"], pose["angle"]))):
                self.rag_pm_drop("non-finite pose")
                return
            # --- window placement: her pivot (the pelvis) follows the body ---
            # render_frame rotates the model about the pelvis, so the pelvis has to
            # land exactly where physics says it is, and her feet are a fixed
            # distance below it (measured off the base pose when she went limp).
            self.rg_x, self.rg_y = pose["x"], pose["y"]
            self.rg_vx, self.rg_vy, self.rg_w = pose["vx"], pose["vy"], pose["w"]
            self.rg_a = -pose["phi"][sk.root]        # render_frame does rot_z4(-rg_a)
            self.rag_contact = sk.touching()
            self.set_feet(pose["x"], pose["y"] + sk.drop)
            self.vx, self.vy = pose["vx"], pose["vy"]   # hair / skirt / tie springs
            self.rag_pm_impacts()
            # --- rest detection -> auto get-up ---
            if self.rag_contact and sk.speed() < ragphys.RAGP_REST_SPEED * s:
                self.rag_rest += dt
            else:
                self.rag_rest = 0.0
            if self.rag_want_up and self.rag_rest > 0.2:
                self.rag_getup()
            elif self.cfg.get("ragdoll_auto_getup", True) and self.rag_rest > RAG_REST_TIME:
                self.rag_getup()
            self.rag_accel(dt, v0x, v0y, w0)
        except Exception:
            traceback.print_exc()
            self.rag_pm_drop("ragdoll_step_pm raised")

    def rag_pm_cursor(self, cur):
        """Point the grab joint at the cursor, once per tick.

        Read here rather than in mouseMoveEvent on purpose. Qt only delivers a
        move event when the cursor actually moves, and delivers them in
        bunches when it moves fast, so the raw event stream is a poor thing to
        hand a constraint: it arrives irregularly and stops dead when the mouse
        stops. Sampling QCursor once per tick gives an evenly spaced target
        every frame, which is what makes her follow smoothly instead of
        juddering toward the mouse in steps.
        """
        sk = self.rag_skel
        if sk is not None and self.dragging and sk.grab_joint is not None:
            sk.move_grab((cur.x(), cur.y()))

    def rag_pm_impacts(self):
        """Turn physics hits into the squash, the dust puff and the sound."""
        W = self.A.rag_world
        if W is None:
            return
        for im in W.pop_impacts(self.rag_owner):
            if im.strength < 0.05:
                continue
            self.sq = min(ragphys.RAGP_SQUASH_MAX, im.strength / ragphys.RAGP_SQUASH_DIV)
            if im.strength > ragphys.RAGP_IMPACT_SOUND:
                play = getattr(self.A, "play_rag_sound", None)
                if play:
                    play()
            if self.rag_hit_t > ragphys.RAGP_IMPACT_COOLDOWN:
                self.rag_hit_t = 0.0
                lx, ly = im.point[0] - self.px, im.point[1] - self.py
                self.emit("puff", 5, lx, ly, spread=90)

    def rag_pm_failsafe(self, sk):
        """Catch the three ways physics can go wrong, and undo all of them."""
        gh = self.A.resolve_screen()
        if sk.explode_guard():
            self.rag_pm_drop("non-finite body")
        elif sk.outside((gh.left(), gh.top(), gh.right(), gh.bottom()),
                        self.H * Renderer.FRAC):
            sk.snap_inside((gh.left(), gh.top(), gh.right(), gh.bottom()),
                           self.H * Renderer.FRAC)

    def rag_pm_drop(self, why):
        """Give up on physics for this pet and put her back on her feet."""
        print(f"[ragdoll] {self.name}: {why}; standing her back up")
        self.rag_clear(); self.reset_position()

    def rag_getup_step_pm(self, dt):
        """Blend the frozen lying pose back to standing, then rag_end."""
        k = min(1.0, self.rag_t / RAG_GETUP_TIME); sm = k * k * (3 - 2 * k)
        ground, surf = self.rag_ground(self.rg_x, self.rg_y0 + self.rag_drop)
        self.rg_strength = 1 - sm
        # every bone unwinds from where she was lying to straight
        self.rag_phi = [p * (1 - sm) for p in self.rag_phi0]
        self.rg_a = self.rag_a0 * (1 - sm)
        self.rg_y = self.rg_y0 + (ground - self.rag_drop - self.rg_y0) * sm
        self.rg_vx = self.rg_vy = self.rg_w = 0.0
        self.rg_top = ground
        self.rag_contact = True
        self.set_feet(self.rg_x, self.rg_y + self.rag_drop)
        self.vx = self.vy = 0.0
        if k >= 1.0:
            self.rag_phi = None
            self.rag_end(surf)

    def rag_accel(self, dt, v0x, v0y, w0):
        """Smoothed body accelerations: these are what whip the limbs on impact."""
        inv = 1 / max(dt, 1e-4); G = self.G; k = 1 - math.exp(-dt * 25)
        self.rg_ax    += (clamp((self.rg_vx - v0x) * inv, -8 * G, 8 * G) - self.rg_ax) * k
        self.rg_ay    += (clamp((self.rg_vy - v0y) * inv, -8 * G, 8 * G) - self.rg_ay) * k
        self.rg_alpha += (clamp((self.rg_w - w0) * inv, -250, 250) - self.rg_alpha) * k

    def rag_drag_step(self, dt):
        """User is holding a ragdolled pet: she hangs (existing roll/pitch code),
        limbs still flop because rag_limb_post keeps running off rg_vx/rg_vy."""
        ch, rad, drop, a = self.rag_geom()
        v0x, v0y = self.rg_vx, self.rg_vy
        self.rg_vx = (self.px - self.last_px) / max(dt, 1e-3)
        self.rg_vy = (self.py - self.last_py) / max(dt, 1e-3)
        fx, fy = self.feet_pos(); self.rg_x, self.rg_y = fx, fy - drop
        self.rg_a = self.rg_w = 0.0; self.rag_contact = False; self.rag_top = None
        self.vx, self.vy = self.rg_vx, self.rg_vy
        self.rag_accel(dt, v0x, v0y, 0.0)

    # ---------------- ragdoll: layer 2, limbs as damped pendulums ----------------
    def rag_limb_post(self, dt):
        """The rig's `post` list for this frame.

        With the articulated engine this is the physics pose written straight
        onto the bones (rag_post_pm). Otherwise it is the legacy layer-2 model:
        one angle and one velocity per limb, driven by apparent gravity, the
        body's acceleration and its spin.
        """
        if self.rag_skel is not None:
            return self.rag_post_pm()
        if self.rag_phi is not None:                 # get-up blend, no skeleton left
            return self.rag_post_getup()
        if not self.rag_bones: return []
        # world_base, not world: measuring against the posed world would let each
        # limb's own swing tilt its parent's pendulum (see Rig.evaluate).
        W, Mn = self.rig.world_base, self.last_M; R = self.ch.renderer; G = self.G
        if Mn is None or not np.all(np.isfinite(Mn)): return []
        # rotating about the SCREEN Z axis, expressed in model space
        # (robust to face_yaw / yaw_offset / the body rotation itself)
        axis = Mn[:3, :3].T @ np.array([0.0, 0.0, 1.0]); axis = axis / (np.linalg.norm(axis) + 1e-12)
        # apparent gravity in screen space (y up), in units of g. Free fall -> (0, 0): limbs float.
        gx, gy = -self.rg_ax / G, -1.0 + self.rg_ay / G
        target = 0.15 if self.rag_contact else 1.0          # lying on the floor: arms relax, don't sink through it
        self.rg_grav += (target - self.rg_grav) * (1 - math.exp(-dt * 6))
        grav = self.rg_grav if self.rag_state == "fall" else 0.0
        n = max(1, int(math.ceil(dt * 90.0))); h = dt / n
        wpp = R.world_per_px * max(1e-6, self.dpr)
        post = []
        for L in self.rag_bones:
            pb = Mn @ np.append(W[L["b"]][:3, 3], 1.0); pc = Mn @ np.append(W[L["c"]][:3, 3], 1.0)
            d0 = pc[:2] - pb[:2]; ln = float(np.hypot(d0[0], d0[1]))
            if ln < 1e-9: continue
            d0 /= ln
            ln_px = ln / wpp
            K = min(80.0, G / max(30.0, ln_px))            # pendulum gain = g / length
            k_soft = L["k"] * (1.0 + 2.0 * (1.0 - self.rg_grav))
            for _ in range(n):
                # Where the limb points right now: the base direction turned by the
                # angle we are about to apply. pose_world rotates every bone rigidly
                # about its own position, so this is exactly what ends up on screen.
                c_, s_ = math.cos(L["d"]), math.sin(L["d"])
                dx, dy = d0[0] * c_ - d0[1] * s_, d0[0] * s_ + d0[1] * c_
                cross = dx * gy - dy * gx                                   # >0 = pulls limb CCW
                acc = K * grav * cross + self.rg_alpha - k_soft * L["d"] - L["damp"] * L["dd"]
                L["dd"] += acc * h; L["d"] += L["dd"] * h
                if abs(L["d"]) > L["lim"]:
                    L["d"] = clamp(L["d"], -L["lim"], L["lim"]); L["dd"] *= -0.3
            post.append((int(L["b"]), rot_axis(axis, L["d"] * self.rag_strength)))
        return post

    def rag_post_axis(self):
        """The screen Z axis expressed in model space, for the post rotations.

        Same expression as the legacy code below: robust to face_yaw, the cfg
        yaw_offset and the body rotation itself, because it is derived from the
        matrix that actually put her on screen.
        """
        Mn = self.last_M
        if Mn is None or not np.all(np.isfinite(Mn)):
            return None
        axis = Mn[:3, :3].T @ np.array([0.0, 0.0, 1.0])
        return axis / (np.linalg.norm(axis) + 1e-12)

    def rag_post_pm(self):
        """Physics pose -> one post rotation per bone.

        For every non-root body, the angle to write is the RELATIVE one:

            post_i = -(phi_i - phi_parent_i)

        Two reasons it is relative rather than absolute. Model.pose_world applies
        each post about that bone's own position and composes down the hierarchy,
        so a child already inherits its ancestors' posts - writing an absolute
        angle would apply the parent's rotation twice. And the root itself is not
        in the list at all: its figure rotation is render_frame's
        rot_z4(-rg_a), applied to the model matrix, and rg_a = +phi_pelvis.

        The sign is negated because the two spaces disagree. Physics measures
        angles with atan2 in y-DOWN screen px, where positive turns clockwise on
        screen; rot_axis about +Z is right-handed in a y-UP frame, where positive
        turns counter-clockwise on screen. test_ragphys.py 7a/7b/7c pins this
        down against the real rig, and 7c checks the whole chain to 2 px.
        """
        sk = self.rag_skel
        axis = self.rag_post_axis()
        if sk is None or axis is None:
            return []
        phi = sk.pose()["phi"]
        parents, bones = sk.parents, sk.bones
        st = self.rag_strength
        post = []
        for i in range(len(phi)):
            p = parents[i]
            if p < 0:
                continue                            # the root is rg_a's job
            post.append((int(bones[i]), rot_axis(axis, -(phi[i] - phi[p]) * st)))
        return post

    def rag_post_getup(self):
        """The get-up blend: the same relative posts, scaled to zero."""
        axis = self.rag_post_axis()
        phi = self.rag_phi
        if axis is None or not phi:
            return []
        sk = self.rag_pm_bones
        if not sk:
            return []
        post = []
        for i in range(len(phi)):
            p = sk["parents"][i]
            if p < 0:
                continue
            post.append((int(sk["bones"][i]), rot_axis(axis, -(phi[i] - phi[p]))))
        return post

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
        now = time.time()
        self.gaze_logic(dt, cur, now)
        self.chat_logic(dt, now)
        self.chat_t -= dt
        if self.chat_t <= 0:
            self.chat_t = random.uniform(30, 80)
            if self.grounded and not self.bubble and self.state == "idle" and not self.in_chat(now):
                self.say(self.line("idle"))
        if self.bubble and now - self.bubble[1] > self.bubble[2]: self.bubble = None; self.mask_dirty = True
        if self.spin_t >= 0:
            self.spin_t += dt
            if self.spin_t > 1.1: self.spin_t = -1.0
        for q in self.parts:
            q["life"] += dt
            if q["life"] < 0: continue                     # hasn't been reached by the gust yet
            q["x"] += q["vx"] * dt; q["y"] += q["vy"] * dt
            if q["kind"] == "wind":
                k = math.exp(-1.4 * dt); q["vx"] *= k; q["vy"] *= k      # straight, just slows
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
        if self.dragging or self.state == "shot" or self.state == "fall" or cur_close:
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

    # ---------------- input ----------------
    def cursor_logic(self, cur, dt, now):
        lx, ly = cur.x() - self.px, cur.y() - self.py
        over = False
        if self.alpha is not None and 0 <= lx < self.W and 0 <= ly < self.H:
            h, w = self.alpha.shape
            over = self.alpha[min(h - 1, int(ly * h / self.H)), min(w - 1, int(lx * w / self.W))] > 16
        self.hover = over and not self.dragging
        if sys.platform == "win32" and self.cfg["click_through"] and not self.A.args.no_mask:
            bg = self.bubble_geom()
            on_bubble = bg is not None and bg[0].contains(QPointF(lx, ly))
            self.set_passthrough(not (over or on_bubble or self.dragging or self.press_g is not None))
        else:
            self.set_passthrough(False)
        if self.hover: self.hover_time += dt
        else: self.hover_time = 0.0; self.hover_said = False
        if self.hover and self.hover_time > 3.5 and not self.hover_said and self.grounded and not self.bubble:
            self.hover_said = True; self.say(self.line("hover"))
        # head-pat: stroke the cursor back and forth over her head
        # An oval, not a circle: the hand moves side to side, and a stroke wide
        # enough to leave a small circle used to wipe the tally before it could
        # ever reach the threshold.  A stroke only counts once it has actually
        # travelled H*0.10 px, so a resting or twitching cursor cannot pat.
        hx, hy = self.head_px
        rx, ry = self.H * 0.20, self.H * 0.15
        near = ((lx - hx) / rx) ** 2 + ((ly - hy) / ry) ** 2 < 1.0
        if near and not self.dragging and self.press_g is None:
            self.pat_leave = 0.0
            self.gaze_peer = None; self.gaze_left = 0.0     # eyes on the user, not a friend
            self.cur_look = True
            dxm = cur.x() - self.last_cur.x()
            if abs(dxm) > 1:
                s = 1 if dxm > 0 else -1
                if self.pat_sign != 0 and s != self.pat_sign:
                    if self.pat_run >= self.H * 0.10: self.pat_events.append(now)
                    self.pat_run = 0.0
                self.pat_sign = s
                self.pat_run += abs(dxm)
            self.pat_events = [t for t in self.pat_events if now - t < 3.2]
            if len(self.pat_events) >= 3 and now > self.pat_cool:
                self.pat_cool = now + 4.0; self.pat_events = []; self.pet()
            if len(self.pat_events) >= 2: self.bow = min(1.0, self.bow + dt * 2.0)
        else:
            # brief slip-off (a bouncy idle, a body in the way) should not throw
            # the whole gesture away - only a real departure does
            self.pat_leave += dt
            if self.pat_leave > 0.30 or self.dragging or self.press_g is not None:
                self.pat_events = []; self.pat_sign = 0; self.pat_run = 0.0
        self.bow = max(0.0, self.bow - dt * 0.8)
        self.last_cur = QPoint(cur)
        if not self.dragging:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.hover else Qt.CursorShape.ArrowCursor)

    def behave(self, dt, cur):
        A, cfg = self.A, self.cfg
        fx, fy = self.feet_pos()
        if self.support is not None:
            gh = A.resolve_screen()
            r = A.tracker.rect(self.support)
            if (r is None or not (r[0] + 4 <= fx <= r[2] - 4)
                    or not (gh.left() + 4 <= fx <= gh.right() - 4)):   # window closed / slid to another monitor
                self.support = None; self.grounded = False; self.vx = self.vy = 0.0
                self.air_kind = "jump"; self.bounces = 0; self.say(self.line("lost"), 1.8)
                self.enter("fall"); return
            fy = r[1]
        else:
            fy = A.floor_y(fx)
        self.set_feet(fx, fy)
        self.state_t += dt; st = self.state
        if st == "shot":
            if self.rig.done:
                self.enter("idle"); self.plan_next()
            return
        if cfg["follow_cursor"] and st in ("idle", "walk", "alert"):
            dx = cur.x() - fx
            if abs(dx) > 120 * cfg["scale"]:
                self.walk_dir = 1 if dx > 0 else -1
                if st != "walk": self.enter("walk", dur=999.0)
                st = "walk"
            elif st == "walk": self.enter("idle"); st = "idle"
        if st == "idle":
            self.plan_t -= dt
            if self.plan_t <= 0:
                # don't wander off mid-conversation
                if self.busy_chat(): self.plan_t = 1.5
                elif cfg["wander"] and not cfg["follow_cursor"]: self.plan_next_action()
                else: self.plan_t = 3.0
        elif st == "alert":
            if self.state_t > self.state_dur: self.enter("idle"); self.plan_next()
        elif st == "walk":
            self.walk(dt); self.pass_greet(dt)

    def plan_next_action(self):
        r = random.random(); fx, _ = self.feet_pos()
        if self.cfg["perch"] and self.A.tracker.enabled and r < 0.18 and self.try_jump(): return
        if r < 0.58:
            g = self.A.resolve_screen(); L, R = g.left(), g.right()
            self.walk_dir = random.choice([-1, 1]) if L + 300 < fx < R - 300 else (1 if fx < (L + R) / 2 else -1)
            self.enter("walk", dur=random.uniform(2.5, 6.0))
        elif r < 0.78: self.enter("alert", dur=random.uniform(3, 5.5))
        else: self.plan_t = random.uniform(2.5, 6.0)

    def walk(self, dt):
        A, cfg = self.A, self.cfg
        fx, fy = self.feet_pos()
        fx += self.walk_dir * cfg["walk_speed"] * cfg["scale"] * dt
        gh = A.resolve_screen(); m = self.W * 0.18
        lo, hi = gh.left() + m, max(gh.left() + m, gh.right() - m)
        if fx < lo: fx = lo; self.walk_dir = 1
        elif fx > hi: fx = hi; self.walk_dir = -1
        if self.support is not None:
            r = A.tracker.rect(self.support)
            if r:
                at_edge = (fx < r[0] + 16 and self.walk_dir < 0) or (fx > r[2] - 16 and self.walk_dir > 0)
                if at_edge:
                    if not self.edge_decided: self.edge_decided = True; self.step_off = random.random() < 0.2
                    if not self.step_off: self.walk_dir *= -1
                else: self.edge_decided = False
        self.set_feet(fx, fy)
        if self.state_t > self.state_dur: self.enter("idle"); self.plan_next()

    # proximity greet: two characters brushing past each other say hello
    def pass_greet(self, dt):
        cfg = self.cfg
        if not cfg["chat"] or not cfg["chat_rate"] or self.dragging: return
        if self.state != "walk" or not self.grounded: return
        if self.busy_chat() or self.meet_target is not None: return
        if random.random() > dt * 0.12 * cfg["chat_rate"]: return
        for q in self.peers(max_dist=self.W * 0.8):
            if q.busy_chat() or q.dragging or not q.isVisible(): continue
            if abs(q.feet_pos()[1] - self.feet_pos()[1]) > self.H * 0.35: continue   # not on the same level
            self.greet(); return

    def air(self, dt):
        A, cfg = self.A, self.cfg
        fx, fy = self.feet_pos()
        self.vy += self.G * dt
        nfx, nfy = fx + self.vx * dt, fy + self.vy * dt
        gh = A.resolve_screen(); m = self.W * 0.15
        lo, hi = gh.left() + m, max(gh.left() + m, gh.right() - m)
        if nfx < lo: nfx = lo; self.vx = abs(self.vx) * 0.55
        elif nfx > hi: nfx = hi; self.vx = -abs(self.vx) * 0.55
        lim = gh.top() - self.H * 0.4
        if nfy < lim: nfy = lim; self.vy = abs(self.vy) * 0.4
        landed = None
        if self.vy >= 0:
            for top, h in A.surfaces(nfx, cfg["perch"]):
                if (h is None and nfy >= top) or (fy <= top + 4 and nfy >= top): landed = (top, h); break
        if landed: self.land(nfx, landed)
        else: self.set_feet(nfx, nfy)

    def land(self, fx, surf):
        top, h = surf; impact = self.vy
        self.set_feet(fx, top)
        if impact > 1000 and self.bounces < 1:
            self.bounces += 1; self.vy = -impact * 0.28; self.vx *= 0.6; self.sq = min(0.22, impact / 7000); return
        self.bounces = 0; self.grounded = True; self.support = h; self.vx = self.vy = 0.0
        self.sq = min(0.26, impact / 6500)
        self.emit("puff", 6, self.W / 2, self.H * (1 - self.PADB), spread=90)
        if self.air_kind == "thrown" and impact > 900:
            self.say(self.line("land")); self.enter("shot", "react")
        else:
            self.enter("idle"); self.plan_t = 1.5
            if h is not None and random.random() < 0.5: self.say(self.line("perch"), 2.0)

    def procedural(self, dt, cur):
        cfg = self.cfg
        hx, hy = self.px + self.head_px[0], self.py + self.head_px[1]
        # look target: another character if one was picked, else the cursor while she is
        # attending to it, else a point off in the room (she is looking at nobody)
        tx_, ty_ = cur.x(), cur.y(); dist = None; idle_target = False
        peer = self.gaze_peer
        if peer is not None and peer.isVisible() and self.gaze_left > 0:
            tx_ = peer.px + peer.head_px[0]; ty_ = peer.py + peer.head_px[1]
            dist = math.hypot(tx_ - hx, ty_ - hy)
        elif not self.cur_look:
            reach = self.W * 2.2
            tx_ = hx + self.idle_pt[0] * reach; ty_ = hy - self.idle_pt[1] * reach
            dist = math.hypot(tx_ - hx, ty_ - hy)
            idle_target = True
        depth = max(self.H * 1.4, dist) if dist is not None else self.H * 1.4
        ax = math.atan2(tx_ - hx, depth); ay = math.atan2(-(ty_ - hy), depth)
        active = self.state in ("idle", "alert", "shot", "walk") \
            and (peer is not None or self.cur_look or idle_target)
        k = 0.25 if self.state == "walk" else (0.3 if self.shot_key == "cutin" and self.state == "shot" else 1.0)
        if idle_target: k *= 0.6                    # an absent glance, not a stare
        tx, ty = (ax * k, ay * k) if active else (0.0, 0.0)
        f = 1 - math.exp(-dt * 7)
        self.gx += (tx - self.gx) * f; self.gy += (ty - self.gy) * f
        if self.state == "walk" or (not self.grounded and self.air_kind == "jump" and not self.dragging):
            by = self.walk_dir * 1.35 if self.state == "walk" else self.walk_dir * 0.9
        elif self.gaze_peer is not None:
            # turn the shoulders toward whoever she is watching, but never past ~45 deg
            by = clamp(self.gx * 0.8, -0.75, 0.75)
        else:
            by = clamp(self.gx * 0.5, -0.55, 0.55)
        self.body_yaw += (by - self.body_yaw) * (1 - math.exp(-dt * 6))
        if self.state == "walk": self.head_yaw = clamp(-self.body_yaw * 0.35 + self.gx, -1.0, 1.0)
        else: self.head_yaw = clamp(self.gx - self.body_yaw, -1.0, 1.0)
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
        yaw_m = self.model.face_yaw + math.radians(cfg["yaw_offset"]) + self.body_yaw
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
        # limb pendulums first: they run off last frame's world pose + last_M,
        # so they are always one frame behind the body - stable and cheap.
        self.rig.rag_post = self.rag_limb_post(self.dt) if self.ragdoll else []
        world = self.rig.evaluate(self.head_yaw, self.head_pitch)
        yaw = self.model.face_yaw + math.radians(cfg["yaw_offset"]) + self.body_yaw
        if self.spin_t >= 0:
            p = clamp(self.spin_t / 1.1, 0, 1); yaw += math.tau * (p * p * (3 - 2 * p))
        f = R.fit
        feet = np.array([f["cx"], f["miny"], f["cz"]]); top = np.array([f["cx"], f["maxy"], f["cz"]])
        z = self.zoom
        B = translate(feet) @ scale4((1 + 0.5 * self.sq) * z, (1 - self.sq) * z, (1 + 0.5 * self.sq) * z) @ m4(rot_y(yaw)) @ translate(-feet)
        # grab pivot: she hangs from the cursor, so swing rotates about the top
        M = (translate(top) @ m4(rot_x(self.pitch_hold)) @ rot_z4(self.roll) @ translate(-top)) @ B
        if self.ragdoll and (not self.dragging or self.rag_skel is not None):
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
        self.last_M = M
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
            self.head_px = (hxp / self.dpr * (self.W * self.dpr / w), hyp / self.dpr * (self.H * self.dpr / h) - 0.05 * self.H)
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
        bx = (self.W - w) / 2; by = max(4.0, self.H * (1 - self.PADB - Renderer.FRAC) - h - 14)
        return QRectF(bx, by, w, h), r

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
        key = (hash(packed), self.grounded, None if bg is None else (int(bg[0].x()), int(bg[0].y()), int(bg[0].width()), int(bg[0].height())),
               tuple((int(q["x"]), int(q["y"]), q["kind"]) for q in self.parts if q["life"] >= 0))
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
            # A3: particles float outside the silhouette, so fold them into the mask too
            for q in self.parts:
                if q["life"] < 0: continue
                r = (int(q["len"] * 0.6) if q["kind"] == "wind" else int(q["size"] * 2.2)) + 2
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
        if self.img is not None: p.drawImage(QRectF(0, 0, self.W, self.H), self.img)
        for q in self.parts:
            if q["life"] < 0: continue
            al = max(0.0, 1 - (q["life"] / q["max"]) ** 2)
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
        p.end()

    def rag_debug_draw(self, p):
        """--rag-debug: the physics capsules, joints and window boxes, on top.

        Drawn in WINDOW px over the render, so a limb the physics has not caught
        up with shows up at once as a bone poking outside its capsule.
        """
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
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

    # ---------------- mouse ----------------
    def mousePressEvent(self, e):
        self.A.set_active(self)
        if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self.press_g = e.globalPosition().toPoint(); self.press_win = QPoint(int(self.px), int(self.py))
            mods = e.modifiers()
            self.rotating = e.button() == Qt.MouseButton.MiddleButton or bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))
            self.rot_yaw0 = self.cfg["yaw_offset"]; self.hist.clear(); self.moved = False
            self.bubble = None if (self.bubble and self.bubble_geom() and self.bubble_geom()[0].contains(e.position())) else self.bubble

    def mouseMoveEvent(self, e):
        if self.press_g is None: return
        g = e.globalPosition().toPoint(); d = g - self.press_g
        if self.rotating:
            self.moved = True
            self.A.set("yaw_offset", ((self.rot_yaw0 + d.x() * 0.7 + 180) % 360) - 180, refresh=True)
            return
        if not self.dragging and d.manhattanLength() > 6:
            self.dragging = True; self.moved = True; self.grounded = False; self.support = None
            self.chat_abort(); self.meet_target = None
            self.vx = self.vy = 0.0; self.bounces = 0; self.air_kind = "thrown"
            if self.rag_skel is not None:
                # hold whichever limb is nearest the cursor; she hangs and swings
                # from that, and the physics carries the rest of her along
                self.rag_skel.grab((g.x(), g.y()), (g.x(), g.y()))
                self.rag_state = "fall"; self.rag_rest = 0.0
            self.enter("drag"); self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if random.random() < 0.6: self.say(self.line("drag"), 2.0)
            self.audience("drag")
        if self.dragging:
            if self.rag_skel is not None:
                # While she is limp the physics owns her window position: the
                # grab joint moves the BODY, and ragdoll_step moves the window
                # from wherever the body ended up. Moving the window here too
                # would fight it and she would lag behind the cursor.
                #
                # The cursor TARGET is deliberately not set here either - see
                # rag_pm_cursor, which reads it once per tick instead. Mouse-move
                # events stop arriving when the mouse stops, and arrive bunched
                # up when it moves fast, so feeding them straight to the grab
                # joint means the target jumps in irregular steps. That is what
                # made her judder under the mouse.
                return
            nx = self.press_win.x() + d.x(); ny = self.press_win.y() + d.y()
            # keep her inside the one screen, horizontally
            g_home = self.A.resolve_screen()
            fx = clamp(nx + self.W / 2, g_home.left() + self.W * 0.10, max(g_home.left() + self.W * 0.10,
                                                                              g_home.right() - self.W * 0.90))
            nx = fx - self.W / 2
            ny = clamp(ny, g_home.top() - self.H * 0.5, g_home.bottom() - self.H * 0.15)
            self.px = nx; self.py = ny
            self.move(int(self.px), int(self.py)); self.hist.append((time.time(), g.x(), g.y()))

    def mouseReleaseEvent(self, e):
        if self.press_g is None: return
        was_drag, rot, moved = self.dragging, self.rotating, getattr(self, "moved", False)
        self.press_g = None; self.rotating = False
        if was_drag:
            self.dragging = False; self.setCursor(Qt.CursorShape.ArrowCursor)
            vx = vy = 0.0; now = time.time()
            pts = [h for h in self.hist if now - h[0] < 0.14]
            if len(pts) >= 2 and pts[-1][0] > pts[0][0]:
                dt = pts[-1][0] - pts[0][0]
                vx, vy = (pts[-1][1] - pts[0][1]) / dt, (pts[-1][2] - pts[0][2]) / dt
            sp = math.hypot(vx, vy)
            if sp < 150: vx = vy = 0.0
            elif sp > 3000: vx, vy = vx * 3000 / sp, vy * 3000 / sp
            self.vx, self.vy = vx, vy; self.air_kind = "thrown"; self.bounces = 0
            if self.ragdoll:                                # thrown while limp: keep ragdolling
                if self.rag_skel is not None:
                    # Just let go. Her bodies already carry the throw momentum
                    # from being dragged through the world, so overwriting their
                    # velocities here would throw away the swing - but a fling
                    # can still build up more speed than the old rod ever could,
                    # so cap it at the same 3000 px/s the rod used. release_grab
                    # does the capping itself, limb by limb.
                    self.rag_skel.release_grab()
                    self.rag_state = "fall"; self.rag_rest = 0.0; self.rag_strength = 1.0
                    return
                self.rg_vx, self.rg_vy = vx, vy
                self.rg_w = clamp(vx * 0.004, -10, 10)
                self.rag_state = "fall"; self.rag_rest = 0.0; self.rag_strength = 1.0
                return
            self.enter("fall")
        elif not rot and not moved:
            self.poke()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self.do_cutin(); return
        if e.button() == Qt.MouseButton.MiddleButton and self.cfg.get("blow_mode"):
            try: self.A.spawn_gust(e.globalPosition().toPoint())
            except Exception: pass

    def wheelEvent(self, e):
        dy = e.angleDelta().y() / 120.0
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.A.set("yaw_offset", ((self.cfg["yaw_offset"] + dy * 15 + 180) % 360) - 180, refresh=True)  # this one only
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.A.set("yaw_offset", ((self.cfg["yaw_offset"] + dy * 15 + 180) % 360) - 180, refresh=True)  # everyone
        else:
            self.A.set("scale", clamp(self.cfg["scale"] * (1.06 ** dy), 0.5, 2.5), refresh=True)

    def contextMenuEvent(self, e):
        self.A.set_active(self)
        self.A.show_menu(e.globalPos(), self)


# =============================================================================
#  Control panel (tabs)
# =============================================================================
QSS = """
QWidget { font-family: 'Segoe UI','Noto Sans',sans-serif; font-size: 12px; color:#1d2b45; }
QWidget#root { background:#eef4fb; }
QTabWidget::pane { border:1px solid #c9daf0; border-radius:8px; background:white; top:-1px; }
QTabBar::tab { padding:7px 18px; background:#dbe8f7; border-top-left-radius:8px; border-top-right-radius:8px; margin-right:2px; color:#3a5a86; }
QTabBar::tab:selected { background:#128cff; color:white; font-weight:600; }
QPushButton { background:#128cff; color:white; border:none; border-radius:8px; padding:7px 12px; font-weight:600; }
QPushButton:hover { background:#3aa1ff; } QPushButton:pressed { background:#0b6fd0; }
QPushButton#alt { background:#dbe8f7; color:#1d4e89; } QPushButton#alt:hover { background:#c7ddf5; }
QSlider::groove:horizontal { height:6px; background:#d6e4f5; border-radius:3px; }
QSlider::sub-page:horizontal { background:#128cff; border-radius:3px; }
QSlider::handle:horizontal { background:white; border:2px solid #128cff; width:14px; margin:-6px 0; border-radius:8px; }
QComboBox { border:1px solid #bcd3ee; border-radius:6px; padding:4px 8px; background:white; }
QMenu { background:white; border:1px solid #bcd3ee; padding:4px; }
QMenu::item { padding:6px 24px; border-radius:4px; } QMenu::item:selected { background:#128cff; color:white; }
QMenu::separator { height:1px; background:#dbe8f7; margin:4px 8px; }
QLabel#title { font-size:16px; font-weight:700; color:white; }
QLabel#status { font-family:Consolas,monospace; background:#f2f7fd; border:1px solid #d3e3f6; border-radius:6px; padding:6px; }
"""

class Panel(QtWidgets.QWidget):
    def __init__(self, A):
        super().__init__()
        self.A = A; self.ctrl = {}
        self.setObjectName("root"); self.setWindowTitle("Desktop Pets - Control Panel"); self.resize(520, 660)
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(10, 10, 10, 10)
        head = QtWidgets.QFrame(); head.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0b4fb3,stop:1 #35b6ff); border-radius:10px;")
        hl = QtWidgets.QHBoxLayout(head)
        self.title = QtWidgets.QLabel("Desktop Pets"); self.title.setObjectName("title"); hl.addWidget(self.title)
        lay.addWidget(head)
        tabs = QtWidgets.QTabWidget(); lay.addWidget(tabs, 1)
        tabs.addTab(self.tab_chars(), "Characters"); tabs.addTab(self.tab_pet(), "Pet")
        tabs.addTab(self.tab_shader(), "Shader"); tabs.addTab(self.tab_interact(), "Interact")
        tabs.addTab(self.tab_help(), "Help")
        self.status = QtWidgets.QLabel(""); self.status.setObjectName("status"); lay.addWidget(self.status)

    # ---- widgets helpers ----
    def check(self, lay, label, key):
        cb = QtWidgets.QCheckBox(label); cb.setChecked(bool(self.A.cfg[key]))
        cb.toggled.connect(lambda v, k=key: self.A.set(k, v)); lay.addWidget(cb); self.ctrl[key] = ("check", cb)

    def slider(self, lay, label, key, lo, hi, mult, fmt="{:.2f}"):
        row = QtWidgets.QHBoxLayout(); lb = QtWidgets.QLabel(label); lb.setMinimumWidth(135)
        sl = QtWidgets.QSlider(Qt.Orientation.Horizontal); sl.setRange(int(lo * mult), int(hi * mult))
        sl.setValue(int(self.A.cfg[key] * mult)); vl = QtWidgets.QLabel(fmt.format(self.A.cfg[key])); vl.setMinimumWidth(46)
        def ch(v, k=key, m=mult, vl=vl, fmt=fmt): vl.setText(fmt.format(v / m)); self.A.set(k, v / m)
        sl.valueChanged.connect(ch)
        row.addWidget(lb); row.addWidget(sl, 1); row.addWidget(vl); lay.addLayout(row)
        self.ctrl[key] = ("slider", sl, mult, vl, fmt)

    def button(self, text, fn, alt=False):
        # clicked(bool) into a plain callable: Qt fills any parameter it can, so a
        # one-argument lambda would be handed `False` and blow up. Take no args.
        if callable(fn):
            def go(_=None, _f=fn):
                _f()
            fn = go
        b = QtWidgets.QPushButton(text); b.clicked.connect(fn)
        if alt: b.setObjectName("alt")
        return b

    def refresh(self):
        for key, c in self.ctrl.items():
            v = self.A.cfg[key]
            if c[0] == "check":
                c[1].blockSignals(True); c[1].setChecked(bool(v)); c[1].blockSignals(False)
            else:
                c[1].blockSignals(True); c[1].setValue(int(v * c[2])); c[1].blockSignals(False); c[3].setText(c[4].format(v))
        self.preset_box.blockSignals(True); self.preset_box.setCurrentText(self.A.cfg["preset"]); self.preset_box.blockSignals(False)

    # ---- tabs ----
    def tab_chars(self):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Screen"))
        self.screen_box = QtWidgets.QComboBox()
        for i, s in enumerate(self.A.qapp.screens()):
            g = s.availableGeometry()
            self.screen_box.addItem(f"{i}: {s.name()}  ({g.width()}x{g.height()})", i)
        self.screen_box.addItem("Cursor screen at startup", "cursor")
        self.screen_box.addItem("Primary screen", "primary")
        cur = self.A.cfg.get("screen", "cursor")
        idx = self.screen_box.findData(cur)
        self.screen_box.setCurrentIndex(idx if idx >= 0 else self.screen_box.count() - 2)
        self.screen_box.currentIndexChanged.connect(self.on_screen_changed)
        row.addWidget(self.screen_box, 1)
        bt = self.button("Apply", self.on_screen_apply, True); row.addWidget(bt)
        l.addLayout(row)
        self.screen_note = QtWidgets.QLabel(""); self.screen_note.setWordWrap(True); l.addWidget(self.screen_note)
        self.sync_screen_note()
        l.addSpacing(6)
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Act on"))
        self.who_box = QtWidgets.QComboBox()
        self.who_box.addItems(["All characters"] + [p.name for p in self.A.pets])
        self.who_box.currentIndexChanged.connect(lambda _i: self.A.set_target(self.who_box.currentIndex()))
        row.addWidget(self.who_box, 1); l.addLayout(row)
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Selected"))
        self.sel_box = QtWidgets.QComboBox()
        self.sel_box.addItems([p.name for p in self.A.pets] or ["-"])
        self.sel_box.currentIndexChanged.connect(lambda i: self.A.set_active_by_index(max(0, i)))
        row.addWidget(self.sel_box, 1); l.addLayout(row)
        l.addSpacing(6)
        self.char_rows = []
        for p in self.A.pets:
            box = QtWidgets.QGroupBox(p.name); g = QtWidgets.QGridLayout(box)
            cbs = QtWidgets.QCheckBox("visible"); cbs.setChecked(p.isVisible())
            cbs.toggled.connect(lambda v, pp=p: self.A.set_pet_visible(pp, v))
            g.addWidget(cbs, 0, 0)
            g.addWidget(self.button("Reset pos", lambda pp=p: pp.reset_position(), True), 0, 1)
            g.addWidget(self.button("Summon", lambda pp=p: self.A.summon(pp), True), 0, 2)
            g.addWidget(self.button("Poke", lambda pp=p: pp.poke(), True), 0, 3)
            g.addWidget(self.button("Say", lambda pp=p: pp.say(pp.line("idle", "poke")), True), 0, 4)
            g.addWidget(self.button("Spin", lambda pp=p: pp.spin(), True), 0, 5)
            l.addWidget(box)
            self.char_rows.append((p, cbs))
        l.addWidget(self.button("Show all", lambda: self.A.set_all_visible(True)))
        l.addWidget(self.button("Hide all", lambda: self.A.set_all_visible(False)))
        note = QtWidgets.QLabel("Tip: if no characters appear at startup, something was left "
                                "hidden last time. Press <b>Show all</b>.")
        note.setWordWrap(True); l.addWidget(note)
        l.addStretch(1); return w

    def sync_char_rows(self):
        for p, cb in getattr(self, "char_rows", ()):
            cb.blockSignals(True); cb.setChecked(p.isVisible()); cb.blockSignals(False)

    def sync_screen_note(self):
        if getattr(self, "screen_note", None):
            g = self.A.resolve_screen()
            self.screen_note.setText(f"All characters are locked to screen {self.A.screen_label()}  "
                                     f"(x {g.left()}..{g.right()}, y {g.top()}..{g.bottom()}).")

    def on_screen_changed(self, _i): self.sync_screen_note()

    def on_screen_apply(self):
        self.A.set_screen(self.screen_box.currentData())
        self.sync_screen_note()

    def tab_pet(self):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        self.slider(l, "Size", "scale", 0.5, 2.5, 100)
        self.slider(l, "Walk speed", "walk_speed", 20, 220, 1, "{:.0f}")
        self.slider(l, "Model rotation (deg)", "yaw_offset", -180, 180, 1, "{:.0f}")
        l.addSpacing(6)
        for label, key in (("Wander around on her own", "wander"), ("Look at my cursor", "look_at_cursor"),
                           ("Follow my cursor (walk to it)", "follow_cursor"),
                           ("Perch / jump on top of my windows (Windows only)", "perch"),
                           ("Speech bubbles", "speech"), ("Particles (hearts / sparkles)", "particles"),
                           ("Always on top", "always_on_top"), ("Click-through transparent areas", "click_through")):
            self.check(l, label, key)
        l.addSpacing(6)
        l.addWidget(QtWidgets.QLabel("Together"))
        self.check(l, "Look at other characters", "peer_look")
        self.slider(l, "How often they glance over", "peer_look_rate", 0.0, 1.0, 100, "{:.2f}")
        self.slider(l, "Cursor notice range (widths)", "cursor_gaze_range", 0.5, 8.0, 10, "{:.1f}")
        self.slider(l, "Still-noticed when far", "cursor_gaze_far", 0.0, 1.0, 100, "{:.2f}")
        self.check(l, "Talk to each other", "chat")
        self.slider(l, "Chattiness", "chat_rate", 0.0, 1.0, 100, "{:.2f}")
        self.check(l, "Walk over before talking", "chat_meetup")
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Frame rate"))
        self.fps_box = QtWidgets.QComboBox(); self.fps_box.addItems(["30", "60"]); self.fps_box.setCurrentText(str(int(self.A.cfg["fps"])))
        self.fps_box.currentTextChanged.connect(lambda s: self.A.set("fps", int(s))); row.addWidget(self.fps_box); row.addStretch(1); l.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.button("Flip 180°", lambda: self.A.set("yaw_offset", ((self.A.cfg["yaw_offset"] + 180 + 180) % 360) - 180, refresh=True), True))
        row.addWidget(self.button("Reset positions", lambda: [p.reset_position() for p in self.A.pets], True))
        row.addWidget(self.button("Hide to tray", lambda: self.A.toggle_pet(), True)); l.addLayout(row)
        l.addStretch(1); return w

    def tab_shader(self):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Preset"))
        self.preset_box = QtWidgets.QComboBox(); self.preset_box.addItems(list(PRESETS)); self.preset_box.setCurrentText(self.A.cfg["preset"])
        self.preset_box.currentTextChanged.connect(self.A.apply_preset); row.addWidget(self.preset_box, 1); l.addLayout(row)
        self.slider(l, "Shadow threshold", "toon_threshold", 0.05, 0.95, 100)
        self.slider(l, "Shadow softness", "toon_softness", 0.002, 0.30, 1000, "{:.3f}")
        self.slider(l, "Shadow strength", "shadow_strength", 0.0, 1.0, 100)
        self.slider(l, "Deep shadow band", "deep_shadow", 0.0, 1.0, 100)
        self.slider(l, "Light intensity", "exposure", 0.4, 1.5, 100)
        self.slider(l, "Rim light", "rim", 0.0, 1.5, 100)
        self.slider(l, "Specular tick", "spec", 0.0, 1.0, 100)
        self.slider(l, "Saturation", "saturation", 0.4, 1.7, 100)
        self.slider(l, "Outline width (px)", "outline_px", 0.0, 5.0, 100)
        self.slider(l, "Outline darkness", "outline_dark", 0.0, 1.0, 100)
        self.check(l, "Eyes through bangs (experimental)", "eyes_through_bangs")
        self.check(l, "Light follows my cursor (3D point light)", "light_follow")
        self.slider(l, "Manual light azimuth", "light_az", -180, 180, 1, "{:.0f}")
        self.slider(l, "Manual light elevation", "light_el", -20, 85, 1, "{:.0f}")
        l.addStretch(1); return w

    def tab_interact(self):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        note = QtWidgets.QLabel("Applies to the target chosen on the Characters tab (All = everyone).")
        note.setWordWrap(True); l.addWidget(note)
        grid = QtWidgets.QGridLayout()
        def run(fn):
            self.A.each(fn)
        items = [("Poke  (Cafe_Reaction)", lambda p: p.poke()), ("Pet  (patting)", lambda p: p.pet()),
                 ("EX Cut-in  (Exs_Cutin)", lambda p: p.do_cutin()), ("Tactical Start", lambda p: p.do_tactical()),
                 ("Spin 360° (3D)", lambda p: p.spin()), ("Launch!", lambda p: p.launch()),
                 ("Ragdoll  (go limp)", lambda p: p.start_ragdoll()),
                 ("Cafe_Idle", lambda p: p.play_anim("idle")), ("Cafe_Walk", lambda p: p.play_anim("walk")),
                 ("Formation_Idle", lambda p: p.play_anim("alert")), ("Formation_Pickup", lambda p: p.play_anim("pickup"))]
        for i, (t, fn) in enumerate(items):
            grid.addWidget(self.button(t, lambda f=fn: run(f), alt=i >= 7), i // 2, i % 2)
        grid.addWidget(self.button("Say something", lambda: self.A.each(lambda p: p.say(p.line("idle", "poke")))), 6, 0)
        grid.addWidget(self.button("Summon to cursor", lambda: self.A.summon()), 6, 1)
        l.addLayout(grid)
        l.addSpacing(8)
        l.addWidget(QtWidgets.QLabel("Together"))
        h = QtWidgets.QHBoxLayout()
        h.addWidget(self.button("Start a conversation", lambda: self.A.start_chat()))
        h.addWidget(self.button("Introduce everyone", self.A.introduce_all))
        l.addLayout(h)
        h2 = QtWidgets.QHBoxLayout()
        h2.addWidget(self.button("Stop all conversations", self.A.end_all_chats, True))
        l.addLayout(h2)
        self.chat_note = QtWidgets.QLabel(""); self.chat_note.setWordWrap(True); l.addWidget(self.chat_note)
        l.addSpacing(8)
        l.addWidget(QtWidgets.QLabel("Blow"))
        self.check(l, "Blow mode: double-middle-click gusts wind from cursor", "blow_mode")
        self.slider(l, "Gust strength", "blow_strength", 0.2, 2.5, 100, "{:.2f}")
        h3 = QtWidgets.QHBoxLayout()
        h3.addWidget(self.button("Gust at cursor now", lambda: self.A.spawn_gust()))
        l.addLayout(h3)
        l.addStretch(1); return w

    def tab_help(self):
        tb = QtWidgets.QTextBrowser(); tb.setOpenExternalLinks(True)
        tb.setHtml("""<h3>Characters</h3><p>Every <code>.glb</code> file in the <code>model</code> folder becomes its own character with its own
        window, animations, walk cycle and dialogue. Drop another <code>.glb</code> in and restart to add more.</p>
        <h3>One screen only</h3><p>All of them are locked to a single monitor - they spawn on it, walk on it, only jump onto
        windows that sit on it, and can't be dragged off it. Pick which monitor on the Characters tab.</p>
        <h3>Together</h3><p>With more than one character they notice each other:</p><ul>
        <li>They <b>glance at each other</b> between idle animations. Move your cursor near one and she looks at you instead.</li>
        <li>They start <b>conversations</b> on their own - different topics, a few lines each, and whoever is
        listening faces the speaker and reacts. Poke one and a nearby character comments on it.</li>
        <li>When two of them walk past each other they say hello.</li></ul>
        <p>All of it is on the Pet tab (<i>Together</i>) and the Interact tab.</p>
        <h3>Controls</h3><ul>
        <li><b>Move cursor</b> - head and body follow it when it is <i>near</i>; further away
        they mostly glance at a friend or off into the room instead
        (<i>Cursor notice range</i> / <i>Still-noticed when far</i> on the Pet tab).</li>
        <li><b>Click</b> - poke. <b>Double-click</b> - EX cut-in.</li>
        <li><b>Drag</b> - pick up (Formation_Pickup). Release while moving to <b>throw</b>.</li>
<li><b>Right-click &rarr; Ragdoll</b> - she goes limp: tumbles, bounces off the floor and
your window title bars, flops about, lies there a few seconds, then gets back up on her own
(turn <i>Ragdoll: auto get-up</i> off to keep her down). While she's limp, <b>click</b> shoves her
away from the cursor and you can still pick her up and throw her.</li>
<li><b>Ctrl + 1</b> - works from <i>any</i> window, including ones you are typing in:
whatever character is under the cursor goes limp, with a short sound to confirm the hit
(press it again to stand her back up). On bare desktop it does nothing at all. Note that
Ctrl+1 is swallowed while this is running, so the app underneath will not act on it
either.</li>
        <li><b>Double-middle-click</b> - gust of wind from the cursor (needs <i>Blow mode</i> on the Interact tab). Hair and clothes flutter - the body stays put.</li>
        <li><b>Stroke the cursor back and forth over her head</b> - petting. She bows as you go.</li>
        <li><b>Wheel</b> - resize everyone. <b>Shift+wheel / Ctrl+wheel / middle-drag / Ctrl+drag</b> - rotate in 3D.</li>
        <li><b>Right-click a character</b> - its own quick menu. Tray icon: show/hide, panel, quit.</li></ul>
        <p><b>Click-through</b> is a per-pixel Windows hit-test; on Linux/macOS it falls back to a mask region.</p>
        <h3>Windows</h3><p>With <i>Perch</i> on they jump up onto your open windows, walk along their title bars,
        ride them while you move them, and fall if you close or minimise them.</p>
        <h3>Tips</h3><p>If a character faces away from you use <b>Flip 180</b>. Run with <code>--dump</code> to print
        bone / animation / material names for every model.</p>""")
        return tb

    def update_status(self):
        p = self.A.active or (self.A.pets[0] if self.A.pets else None)
        if p is None: return
        n = sum(1 for q in self.A.pets if q.isVisible())
        short = self.A.dialogue.display_name
        if p.gaze_peer is not None and p.gaze_left > 0: looking = f"looking at {short(p.gaze_peer.name)}"
        elif not p.cur_look: looking = "looking off into the room"
        else: looking = "looking at cursor"
        chat = ""
        if p.busy_chat():
            if p.meet_target is not None: chat = f"walking over to {short(p.meet_target.name)}"
            elif p.chat_with is not None:
                chat = f"{self.A.dialogue.topic_label(p.chat_topic)} with {short(p.chat_with.name)}"
        self.status.setText(f"{n} character(s) | screen {self.A.screen_label()} | fps: {self.A.fps:4.0f}\n"
                            f"{p.name}: state {p.state:<6} anim: {ANIM_KEYS[p.anim_key][0]:<17} "
                            f"pos: {int(p.px)},{int(p.py)}  grounded: {p.grounded}  on-window: {p.support is not None}\n"
                            f"{looking}" + (f"  |  {chat}" if chat else ""))
        if getattr(self, "chat_note", None):
            if not self.A.cfg["chat"] or n < 2:
                self.chat_note.setText("Needs 2+ visible characters and <i>Talk to each other</i> on.")
            else:
                self.chat_note.setText(f"Topics: {self.A.dialogue.topic_label_text()}.")


# =============================================================================
#  Application glue
# =============================================================================
def make_icon(letter="A"):
    pm = QtGui.QPixmap(64, 64); pm.fill(Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    g = QtGui.QLinearGradient(0, 0, 64, 64); g.setColorAt(0, QtGui.QColor("#35b6ff")); g.setColorAt(1, QtGui.QColor("#0b4fb3"))
    p.setBrush(QtGui.QBrush(g)); p.setPen(QtGui.QPen(QtGui.QColor("white"), 3)); p.drawEllipse(4, 4, 56, 56)
    p.setPen(QtGui.QColor("white")); f = QtGui.QFont("Arial", 28, QtGui.QFont.Weight.Bold); p.setFont(f)
    p.drawText(QRectF(0, 0, 64, 62), int(Qt.AlignmentFlag.AlignCenter.value), (letter or "A")[:1].upper()); p.end()
    return QtGui.QIcon(pm)


# =============================================================================
#  One character = one .glb + its own animations, renderer and dialogue
# =============================================================================
class Character:
    def __init__(self, path, index=0, dump=False):
        self.path = Path(path)
        self.index = index
        self.name = self.path.stem.replace("_", " ").replace("-", " ").strip() or f"Pet {index + 1}"
        if len(self.name) > 28: self.name = self.name[:28]
        print(f"Loading {self.path.name} as '{self.name}' ...")
        self.model = Model(self.path)
        if dump: self.model.dump()
        self.clips = {}
        fallback = next(iter(self.model.clips.values()), self.model.rest_clip)
        for key, (name, _loop) in ANIM_KEYS.items():
            c = self.model.find_clip(name)
            if c is None:
                print(f"[warn] {self.path.name}: animation '{name}' not found - using '{fallback.name}'.")
                c = fallback
            self.clips[key] = c
        idle = self.clips["idle"]
        self.fit = self.model.compute_fit(self.model.pose_world(idle.T[0], idle.Q[0], idle.S[0]))
        self.renderer = None            # created lazily (one GL context is enough)


class App(QtCore.QObject):
    def __init__(self, qapp, paths, args):
        super().__init__()
        self.qapp, self.args = qapp, args
        self.cfg = dict(DEFAULTS)
        try:
            if SETTINGS_PATH.exists(): self.cfg.update({k: v for k, v in json.loads(SETTINGS_PATH.read_text()).items() if k in DEFAULTS})
        except Exception: pass
        if not isinstance(self.cfg.get("positions"), dict): self.cfg["positions"] = {}
        if not isinstance(self.cfg.get("hidden"), list): self.cfg["hidden"] = []
        if args.scale: self.cfg["scale"] = args.scale
        if args.fps: self.cfg["fps"] = args.fps
        if getattr(args, "rag_debug", False): self.cfg["ragdoll_debug"] = True
        self.rag_trace = int(getattr(args, "rag_trace", 0) or 0)
        if getattr(args, "screen", None) is not None: self.cfg["screen"] = args.screen
        self.home = self.resolve_screen()
        print("Locked to screen:", self.screen_label())
        paths = [Path(p) for p in paths]
        print(f"{len(paths)} model(s) found.")
        self.chars = []
        for i, p in enumerate(paths):
            try:
                self.chars.append(Character(p, i, args.dump))
            except Exception as e:
                print(f"[error] could not load {p.name}: {type(e).__name__}: {e}")
                traceback.print_exc()
        if not self.chars:
            raise RuntimeError("none of the .glb files could be loaded")
        self.ctx = moderngl.create_standalone_context(require=330)
        print("GL:", self.ctx.info.get("GL_RENDERER"))
        self.tracker = WinTracker(); self.trk_t = 0.0
        # One physics world for the whole app, shared by every pet, so two of
        # them can collide with each other. Only exists with pymunk; without it
        # every pet falls back to the legacy rod ragdoll.
        self.rag_world = RagWorld() if HAVE_PYMUNK else None
        if self.rag_world is not None:
            g = self.home
            self.rag_world.set_screen((g.left(), g.top(), g.right() + 1, g.bottom() + 1))
        self.trk_wins = None                              # last window list we synced
        self.cutin = Cutin()
        self.gusts = []                        # active wind bursts: dicts(x, y, t0)
        # dialogue: the lines, the computer-state tags, and who talks to whom
        self.dialogue = DialogueEngine()
        # the watcher polls on its own thread so a slow sample can never
        # stutter the render loop
        self.watcher = SystemWatcher(priority=self.dialogue.state_order,
                                     threaded=True,
                                     debug=bool(getattr(args, "debug_tags", False)))
        self.chats = ChatDirector(self)
        self.pets = []
        self.z_order = []                    # pets by bubble age, oldest first
        self.z_t = 0.0
        self.active = None
        self.target = -1                                   # -1 = all characters
        hidden = set(self.cfg["hidden"])
        print(f"Saved hidden characters: {len(hidden)} ({', '.join(sorted(hidden)) or 'none'})")
        for i, ch in enumerate(self.chars):
            ch.renderer = Renderer(ch.model, ch.fit, self.ctx)
            pet = Pet(self, ch)
            self.pets.append(pet)
        self.active = self.pets[0]
        self.panel = Panel(self)
        self.setup_tray()
        self.save_timer = QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.timeout.connect(self.save)
        # one pet reacts to a computer-state change, e.g. opening a game
        self.reactor = StateReactor(self.dialogue, self.watcher, self.pets,
                                    clock=time.perf_counter)
        self.last = time.perf_counter(); self.fps = 30.0; self.status_t = 0.0; self.errors = 0
        self.timer = QTimer(self); self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick); self.timer.start(int(1000 / self.cfg["fps"]))
        qapp.aboutToQuit.connect(self.save)
        for i, p in enumerate(self.pets):
            p.spawn(i)
        # apply the saved hidden state only once every pet has spawned - doing it
        # inside the loop above judged later pets while they were still invisible
        self.apply_hidden_state(hidden)
        self.setup_hotkey()
        self.setup_rag_sound()
        qapp.aboutToQuit.connect(self.shutdown_hotkey)

    # ---- global Ctrl+1: flop the character under the cursor ----
    def setup_hotkey(self):
        """System-wide Ctrl+1, with an in-app shortcut as the fallback.

        The hook only raises a flag (see GlobalHotkey); tick() does the work, so no
        Qt call ever happens inside a low-level keyboard callback.
        """
        self.hotkey = GlobalHotkey(self)
        if not self.hotkey.enabled:
            # No hook (or Windows said no): Ctrl+1 still works while this app has
            # focus, which is the whole app rather than one window.
            sc = QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+1"), self.active)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(self.hotkey_fire)
            self.hotkey_shortcut = sc
            print("[info] Ctrl+1 works while the pets have focus")

    def setup_rag_sound(self):
        """The sound that plays when the hotkey lands on a character. Never fatal."""
        self.rag_player = None
        if not HAVE_AUDIO:
            print("[warn] no Qt multimedia - Ctrl+1 will be silent")
            return
        for cand in (HERE / RAG_SOUND, HERE / "sounds" / RAG_SOUND, Path(RAG_SOUND)):
            try:
                if cand.is_file():
                    # Qt 6 keeps the volume on the audio output, not the player.
                    out = QAudioOutput(); out.setVolume(RAG_SOUND_VOLUME)
                    pl = QMediaPlayer(); pl.setAudioOutput(out)
                    pl.setSource(QtCore.QUrl.fromLocalFile(str(cand.resolve())))
                    self.rag_player, self.rag_output = pl, out
                    print(f"[info] hotkey sound: {cand.name}")
                    return
            except Exception as e:
                print(f"[warn] hotkey sound {cand} unusable: {e}")
        print(f"[warn] {RAG_SOUND} not found - Ctrl+1 will be silent")

    def play_rag_sound(self):
        pl = self.rag_player
        if pl is None: return
        try:
            pl.setPosition(0)                    # replay immediately if still going
            pl.play()
        except Exception as e:
            print(f"[warn] could not play {RAG_SOUND}: {e}")

    def shutdown_hotkey(self):
        hk = getattr(self, "hotkey", None)
        if hk is not None:
            hk.fired = False; hk.remove()
        pl = getattr(self, "rag_player", None)
        if pl is not None:
            try: pl.stop(); pl.setSource(QtCore.QUrl())
            except Exception: pass

    def pet_under_cursor(self):
        """The visible character whose pixels the cursor is on, or None.

        Same per-pixel test cursor_logic() uses for hover, so the hotkey picks the
        character you can actually click, not the one whose window box you are over.
        Two characters can overlap; the nearer head wins.
        """
        cur = QtGui.QCursor.pos(); best, bd = None, None
        for p in self.pets:
            if not p.isVisible(): continue
            lx, ly = cur.x() - p.px, cur.y() - p.py
            if not (0 <= lx < p.W and 0 <= ly < p.H): continue
            a = p.alpha
            if a is not None:
                h, w = a.shape
                if a[min(h - 1, int(ly * h / p.H)), min(w - 1, int(lx * w / p.W))] <= 16: continue
            d = math.hypot(lx - p.head_px[0], ly - p.head_px[1])
            if bd is None or d < bd: bd, best = d, p
        return best

    def hotkey_fire(self):
        """Ctrl+1: ragdoll whoever is under the cursor, with a sound for the hit.

        The sound is the confirmation that the press landed on somebody. Bare
        desktop is silent - there is nothing there to confirm, and a "you missed"
        noise on every stray press just gets irritating.
        """
        p = self.pet_under_cursor()
        if p is None: return None
        if p.ragdoll: p.rag_getup()              # already limp: stand her back up
        else: p.start_ragdoll()
        try: p.raise_()
        except Exception: pass
        self.play_rag_sound()
        return p

    # ---- character selection ----
    def set_active(self, pet):
        if pet is not self.active:
            self.active = pet
            if getattr(self.panel, "sel_box", None):
                i = self.pets.index(pet) if pet in self.pets else -1
                self.panel.sel_box.blockSignals(True); self.panel.sel_box.setCurrentIndex(i); self.panel.sel_box.blockSignals(False)

    def set_active_by_index(self, i):
        if 0 <= i < len(self.pets): self.set_active(self.pets[i])

    def set_target(self, i):
        self.target = i

    def each(self, fn):
        for p in self.targets(): fn(p)

    def targets(self):
        if 0 <= self.target < len(self.pets): return [self.pets[self.target]]
        return list(self.pets)

    # ---- bubble z-order ----
    def touch_zorder(self, pet):
        """Record that `pet` just started talking: newest line goes on top."""
        try:
            self.z_order.remove(pet)
        except Exception:
            pass
        self.z_order.append(pet)

    def sort_bubble_zorder(self):
        """Keep the newest bubble above the older ones.

        Window managers are free to reshuffle top-level windows (a new window, a
        menu, an alt-tab), and each pet window is only a few pixels behind another
        one, so the order is re-applied a couple of times a second instead of
        trusting raise() to stick. Only raised while two or more bubbles are actually
        on screen, and always in oldest -> newest order.
        """
        talkers = [p for p in self.z_order if p.bubble is not None and p.isVisible()]
        if len(talkers) < 2: return
        for p in talkers:
            try: p.raise_()
            except Exception: pass
        if len(self.z_order) > 24: self.z_order = talkers

    # ---- pet-to-pet ----
    def peers_of(self, p, max_dist=None, same_surface=False):
        """Other visible characters near `p`, nearest first.

        max_dist      horizontal reach in px (default ~1.6 character widths)
        same_surface  only characters perched on the same window as `p`
        """
        if max_dist is None: max_dist = BASE_W * 1.6 * self.cfg["scale"]
        fx, fy = p.feet_pos(); out = []
        for q in self.pets:
            if q is p or not q.isVisible(): continue
            qx, qy = q.feet_pos()
            d = abs(qx - fx)
            if d > max_dist: continue
            if same_surface and (q.support is not p.support or q.support is None): continue
            if abs(qy - fy) > p.H * 0.6: continue          # not on the same level
            home = getattr(self, "home", None)
            if home is not None:      # single-screen build: never pair up across monitors
                if qx + q.W * 0.5 < home.left() or qx - q.W * 0.5 > home.right(): continue
            out.append((d + abs(qy - fy) * 0.5, q))
        out.sort(key=lambda t: t[0])
        return [q for _k, q in out]

    def start_chat(self, a=None, b=None):
        """Kick off a conversation, either between two given pets or a free pair."""
        return self.chats.start(a, b)

    def introduce_all(self):
        """Round-robin greeting: everyone says hi to the next one."""
        return self.chats.introduce_all()

    def end_all_chats(self):
        return self.chats.end_all()

    def set_screen(self, spec):
        """Move every character onto the chosen screen and keep them there."""
        self.cfg["screen"] = spec
        self.resolve_screen(spec)
        self.end_all_chats()                      # nobody mid-conversation when the screen changes
        g = self.home
        print("Locked to screen:", self.screen_label())
        n = len(self.pets) or 1
        for i, p in enumerate(self.pets):
            frac = 0.9 - i * min(0.75 / n, 0.25)
            p.rag_clear()
            p.support = None; p.grounded = True; p.vx = p.vy = 0
            p.set_feet(self.clamp_x(g.left() + (g.right() - g.left()) * frac, p.W), g.bottom() + 1)
            p.enter("idle")
        self.save_timer.start(600)
        if hasattr(self, "panel"): self.panel.sync_screen_note()

    def set_pet_visible(self, pet, vis):
        """Show/hide one character.

        Only this character's own name is touched - the list is never rebuilt
        from every pet's isVisible(), which used to mark not-yet-spawned pets
        as hidden and cascade into "everything stays hidden" across restarts.
        """
        if vis: pet.show()
        else:
            # hiding her mid-tumble: drop the ragdoll so she comes back as a
            # normal falling pet instead of freezing halfway through a throw
            pet.rag_clear()
            pet.hide()
        names = set(self.cfg.get("hidden") or [])
        if vis: names.discard(pet.name)
        else: names.add(pet.name)
        self.cfg["hidden"] = [p.name for p in self.pets if p.name in names]
        self.save_timer.start(600)
        if getattr(self, "panel", None) and getattr(self.panel, "sync_char_rows", None):
            self.panel.sync_char_rows()

    def set_all_visible(self, vis):
        for p in self.pets: self.set_pet_visible(p, vis)
        if getattr(self.panel, "sync_char_rows", None): self.panel.sync_char_rows()

    def apply_hidden_state(self, hidden):
        """Restore per-character visibility from the saved list (after spawning).

        Two safety nets, both so a bad save can never leave you with a blank screen:
          * stale entries (a character whose .glb is gone) are dropped
          * a list that hides *every* character is the signature of the old
            'Hide to tray' bug, not a real choice, so everybody comes back
        """
        hidden = set(hidden or ())
        known = {p.name for p in self.pets}
        stale = hidden - known
        if known and hidden >= known:
            print("[info] every character was saved as hidden (leftover 'Hide to tray' "
                  "state) - showing them all.")
            hidden = set()
        for p in self.pets:
            p.show() if p.name not in hidden else p.hide()
        self.cfg["hidden"] = sorted(known & hidden)
        if stale:
            print("[info] dropping hidden characters that no longer exist:",
                  ", ".join(sorted(stale)))
        if getattr(self.panel, "sync_char_rows", None): self.panel.sync_char_rows()

    # ---- one screen only: every character is locked to self.home ----
    def resolve_screen(self, spec=None):
        """Pick the single screen the pets live on and cache it as self.home."""
        spec = self.cfg.get("screen", "cursor") if spec is None else spec
        screens = self.qapp.screens()
        home = None
        if isinstance(spec, int) or (isinstance(spec, str) and spec.isdigit()):
            i = int(spec)
            if 0 <= i < len(screens): home = screens[i].availableGeometry()
        if home is None and spec == "primary":
            home = self.qapp.primaryScreen().availableGeometry()
        if home is None:                                   # "cursor" and any bad value
            c = QtGui.QCursor.pos()
            scr = self.qapp.screenAt(c) or self.qapp.primaryScreen()
            home = scr.availableGeometry()
        self.home = home
        return home

    def screen_label(self):
        for i, s in enumerate(self.qapp.screens()):
            if s.availableGeometry() == getattr(self, "home", None):
                return f"{i}: {s.name()} ({s.availableGeometry().width()}x{s.availableGeometry().height()})"
        return "-"

    def bounds(self):
        g = self.resolve_screen()
        return g.left(), g.right() + 1

    def top(self): return self.resolve_screen().top()

    def screen_size(self):
        g = self.resolve_screen(); return g.width(), g.height()

    def floor_y(self, fx):
        return self.resolve_screen().bottom() + 1

    def surfaces(self, fx, perch):
        home = self.resolve_screen()
        res = []
        if perch and self.tracker.enabled:
            for top, h in self.tracker.surfaces(fx):
                if top < home.top() - 4 or top > home.bottom() + 4: continue   # other monitors
                if fx < home.left() + 6 or fx > home.right() - 6: continue
                res.append((top, h))
        res.sort(key=lambda s: s[0]); res.append((self.floor_y(fx), None)); return res

    def clamp_x(self, fx, w):
        """Keep a character's feet inside the home screen."""
        g = self.resolve_screen(); m = w * 0.18
        return clamp(fx, g.left() + m, max(g.left() + m, g.right() - m))

    # ---- settings ----
    def set(self, key, val, refresh=False):
        self.cfg[key] = val; self.save_timer.start(600)
        if key == "scale":
            for p in self.pets: p.apply_scale()
        elif key == "always_on_top":
            for p in self.pets: p.apply_flags()
        elif key == "fps": self.timer.setInterval(int(1000 / max(1, val)))
        elif key == "click_through":
            for p in self.pets: p.mask_dirty = True
        elif key == "perch" and not val: self.tracker.wins = []
        elif key == "screen":
            # the home screen moved, so the physics walls have to move with it
            self.resolve_screen()
            if self.rag_world is not None:
                g = self.home
                self.rag_world.set_screen((g.left(), g.top(), g.right() + 1, g.bottom() + 1))
        if refresh and hasattr(self, "panel"): self.panel.refresh()

    def apply_preset(self, name):
        if name in PRESETS:
            self.cfg.update(PRESETS[name]); self.cfg["preset"] = name; self.save_timer.start(600); self.panel.refresh()

    def save(self):
        try:
            pos = {p.name: p.feet_pos()[0] for p in self.pets}
            self.cfg["positions"] = pos
            if not self.stashed():
                # only remember a character as hidden when the rest are still up;
                # a fully-hidden screen is 'Hide to tray' and is not a real choice
                self.cfg["hidden"] = [p.name for p in self.pets if not p.isVisible()]
            if self.pets: self.cfg["pos_x"] = self.pets[0].feet_pos()[0]
            SETTINGS_PATH.write_text(json.dumps(self.cfg, indent=2))
        except Exception: pass

    # ---- ui ----
    def summon(self, pet=None):
        g = self.resolve_screen()
        c = QtGui.QCursor.pos()
        if not (g.left() <= c.x() <= g.right() and g.top() <= c.y() <= g.bottom()):
            c = g.center()                                   # cursor is on another monitor
        for p in (self.targets() if pet is None else [pet]):
            p.rag_clear()
            p.support = None; p.grounded = False; p.dragging = False
            p.chat_abort(); p.meet_target = None
            fx = self.clamp_x(c.x(), p.W)
            p.set_feet(fx, min(c.y() - 40, g.bottom() - p.H * 0.2))
            p.vx = p.vy = 0; p.air_kind = "thrown"
            p.enter("fall"); p.recall()

    def spawn_gust(self, pos=None):
        """Burst wind out of the cursor: double-middle-click with Blow mode on,
        or the Interact-tab button. Hits every visible pet in radius."""
        try: c = QtGui.QCursor.pos() if pos is None else pos
        except Exception: return
        self.gusts.append(dict(x=float(c.x()), y=float(c.y()), t0=time.time()))
        self.gusts = self.gusts[-6:]                       # never stockpile
        for p in self.pets:
            if p.isVisible():
                p.emit_wind(c.x(), c.y(), float(self.cfg.get("blow_strength", 1.0)))

    def gust_wind_at(self, x, y):
        """Radial wind (px/s) + 0..1 energy at screen point (x, y). Summed over
        active gusts: fast attack, exponential decay, smooth distance falloff."""
        if not self.gusts: return 0.0, 0.0, 0.0
        now = time.time()
        strength = float(self.cfg.get("blow_strength", 1.0))
        self.gusts = [g for g in self.gusts if now - g["t0"] < GUST_DUR + 0.4]
        wx = wy = e = 0.0
        for g in self.gusts:
            age = now - g["t0"]
            if age < 0 or age > GUST_DUR: continue
            dx, dy = x - g["x"], y - g["y"]
            d = math.hypot(dx, dy)
            if d > GUST_RADIUS: continue
            if d < 8.0: dx, dy, d = 0.0, -8.0, 8.0       # dead centre -> push up
            env = min(1.0, age / 0.08) * math.exp(-2.5 * age)
            fall = (1.0 - d / GUST_RADIUS) ** 1.5
            m = GUST_SPEED * strength * env * fall
            wx += dx / d * m; wy += dy / d * m
            e = max(e, min(1.0, m / 900.0))
        return wx, wy, e

    def toggle_pet(self):
        """Hide / show everything. Deliberately NOT persisted - this is a
        temporary 'get them off my screen' action, not a per-character choice.
        """
        vis = not all(p.isVisible() for p in self.pets)
        for p in self.pets: p.show() if vis else p.hide()
        if getattr(self.panel, "sync_char_rows", None): self.panel.sync_char_rows()

    def stashed(self):
        """True while everything is hidden by toggle_pet (so save() can tell them apart)."""
        return bool(self.pets) and not any(p.isVisible() for p in self.pets)

    def show_panel(self):
        self.panel.refresh(); self.panel.sync_char_rows(); self.panel.show(); self.panel.raise_(); self.panel.activateWindow()

    def show_menu(self, pos, p=None):
        p = p or self.active or (self.pets[0] if self.pets else None)
        if p is None: return
        m = QtWidgets.QMenu()
        m.setTitle(p.name)
        for t, fn in (("Poke", p.poke), ("Pet", p.pet), ("EX Cut-in", p.do_cutin),
                      ("Tactical Start", p.do_tactical), ("Spin (3D)", p.spin), ("Launch!", p.launch)):
            m.addAction(t, fn)
        if p.ragdoll: m.addAction("Get up", p.rag_getup)
        else:         m.addAction("Ragdoll", lambda: p.start_ragdoll())
        a = m.addAction("Ragdoll: auto get-up"); a.setCheckable(True)
        a.setChecked(bool(self.cfg.get("ragdoll_auto_getup", True)))
        a.toggled.connect(lambda v: self.set("ragdoll_auto_getup", v, refresh=True))
        am = m.addMenu("Animations")
        for key, (name, _l) in ANIM_KEYS.items(): am.addAction(name, lambda k=key: p.play_anim(k))
        m.addSeparator()
        pm = m.addMenu("Other characters")
        for q in self.pets:
            if q is p: continue
            sm = pm.addMenu(q.name)
            sm.addAction("Make active", lambda pp=q: self.set_active(pp))
            sm.addAction("Summon", lambda pp=q: self.summon(pp))
            sm.addAction("Poke", lambda pp=q: pp.poke())
            sm.addAction("Reset position", lambda pp=q: pp.reset_position())
            a = sm.addAction(f"Talk with {q.name}"); a.setEnabled(self.cfg["chat"])
            a.triggered.connect(lambda _c=False, pp=p, qq=q: self.start_chat(pp, qq))
            a = sm.addAction("Visible"); a.setCheckable(True); a.setChecked(q.isVisible())
            a.toggled.connect(lambda v, pp=q: self.set_pet_visible(pp, v))
        m.addSeparator()
        for label, key in (("Wander", "wander"), ("Look at cursor", "look_at_cursor"), ("Follow cursor", "follow_cursor"),
                           ("Perch on windows", "perch"), ("Speech bubbles", "speech"), ("Particles", "particles"),
                           ("Always on top", "always_on_top"), ("Light follows cursor", "light_follow")):
            a = m.addAction(label); a.setCheckable(True); a.setChecked(bool(self.cfg[key]))
            a.toggled.connect(lambda v, k=key: self.set(k, v, refresh=True))
        sm = m.addMenu("Size")
        for pct in (50, 75, 100, 125, 150, 200): sm.addAction(f"{pct}%", lambda v=pct: self.set("scale", v / 100, refresh=True))
        pm = m.addMenu("Shader preset")
        for name in PRESETS: pm.addAction(name, lambda n=name: self.apply_preset(n))
        m.addSeparator()
        m.addAction("Control Panel...", self.show_panel); m.addAction("Hide to tray", self.toggle_pet); m.addAction("Quit", self.qapp.quit)
        m.exec(pos)

    def setup_tray(self):
        self.tray = None
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.show_panel(); return
        self.tray = QtWidgets.QSystemTrayIcon(make_icon(self.active.name if self.active else "A"), self)
        self.tray.setToolTip("Desktop Pets")
        menu = QtWidgets.QMenu()
        menu.addAction("Show / Hide all", self.toggle_pet); menu.addAction("Control Panel...", self.show_panel)
        menu.addSeparator()
        menu.addAction("Summon target to cursor", self.summon)
        menu.addAction("EX Cut-in (target)", lambda: self.each(lambda p: p.do_cutin()))
        menu.addAction("Poke (target)", lambda: self.each(lambda p: p.poke()))
        cm = menu.addMenu("Characters")
        for p in self.pets:
            sub = cm.addMenu(p.name)
            sub.addAction("Make active", lambda pp=p: self.set_active(pp))
            sub.addAction("Summon", lambda pp=p: self.summon(pp))
            sub.addAction("EX Cut-in", lambda pp=p: pp.do_cutin())
            sub.addAction("Ragdoll", lambda pp=p: pp.start_ragdoll())
            a = sub.addAction("Visible"); a.setCheckable(True); a.setChecked(p.isVisible())
            a.toggled.connect(lambda v, pp=p: self.set_pet_visible(pp, v))
        menu.addSeparator()
        menu.addAction("Quit", self.qapp.quit)
        self.tray_menu = menu; self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_panel() if r in (QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
                                                                          QtWidgets.QSystemTrayIcon.ActivationReason.Trigger) else None)
        self.tray.show()

    # ---- main loop ----
    def tick(self):
        now = time.perf_counter(); dt = min(0.1, now - self.last); self.last = now
        self.fps = 0.9 * self.fps + 0.1 * (1.0 / max(dt, 1e-3))
        # The keyboard hook only raises a flag; the real work happens here, off the
        # hook callback. Checked outside the try below so one bad hotkey target can
        # never take the whole app down.
        hk = getattr(self, "hotkey", None)
        if hk is not None and hk.fired:
            hk.fired = False
            try:
                self.hotkey_fire()
            except Exception:
                traceback.print_exc()
        try:
            if self.cfg["perch"] and self.tracker.enabled and now - self.trk_t > 0.12:
                self.tracker.refresh(self.active.dpr if self.active else 1.0, self.home.top()); self.trk_t = now
            # Physics: window colliders first (only when the list actually
            # changed), then ONE step for the whole app - not one per pet, or
            # four pets on screen would advance the world four times as fast.
            if self.rag_world is not None:
                if self.tracker.wins is not self.trk_wins:
                    self.trk_wins = self.tracker.wins
                    try:
                        self.rag_world.sync_windows(self.tracker.wins, self.trk_t,
                                                    bool(self.cfg["perch"] and self.tracker.enabled))
                    except Exception:
                        traceback.print_exc()
                self.rag_world.step(dt)
            self.watcher.tick()                                # cheap; throttles itself
            self.dialogue.set_context(self.watcher.current_context())
            self.reactor.reconfigure(self.pets)
            self.reactor.poll()
            for p in self.pets:
                if p.isVisible(): p.tick(dt)
            if self.cutin.active: self.cutin.step()
            if now - self.z_t > 0.4: self.z_t = now; self.sort_bubble_zorder()
            if self.panel.isVisible() and now - self.status_t > 0.25: self.panel.update_status(); self.status_t = now
            self.errors = 0
        except Exception:
            traceback.print_exc(); self.errors += 1
            if self.errors > 30:
                print("Too many errors, stopping."); self.timer.stop()


def find_models(args):
    """Every .glb in the model/ folder becomes a character.

    --model still limits or overrides the list: give it a file, or a folder to
    take every .glb from, and neither has to be model/.
    """
    if args.model:
        paths = []
        for part in args.model:
            p = Path(part)
            paths.extend(sorted(p.glob("*.glb")) if p.is_dir() else ([p] if p.exists() else []))
        return paths
    return sorted(MODEL_DIR.glob("*.glb"), key=lambda q: q.name.lower())


def main():
    ap = argparse.ArgumentParser(description="Single-screen desktop pets (one character per .glb in model/)")
    ap.add_argument("--model", nargs="*", help="glb file(s) or folder(s) to use (default: every *.glb in model/)")
    ap.add_argument("--dump", action="store_true", help="print bones / animations / materials for every model")
    ap.add_argument("--no-mask", action="store_true", help="disable click-through mask (whole window clickable)")
    ap.add_argument("--debug-tags", action="store_true",
                    help="print the watcher's tags, matched rule and context every poll "
                         "(console only - window titles are never written to a file)")
    ap.add_argument("--rag-debug", action="store_true",
                    help="draw the ragdoll capsules, joints and window colliders over each character")
    ap.add_argument("--rag-trace", type=int, nargs="?", const=60, default=0, metavar="N",
                    help="print per-frame ragdoll physics for the first N frames (default 60)")
    ap.add_argument("--scale", type=float, default=None); ap.add_argument("--fps", type=int, default=None)
    ap.add_argument("--screen", default=None,
                    help="lock every character to one screen: 0, 1, ... / 'cursor' (default) / 'primary'")
    args = ap.parse_args()
    qapp = QtWidgets.QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False); qapp.setStyleSheet(QSS)
    paths = find_models(args)
    if not paths:
        QtWidgets.QMessageBox.critical(
            None, "Desktop Pets",
            f"No .glb models found in:\n{MODEL_DIR}\n\n"
            f"Put your character models in that folder (it should already exist).")
        return 1
    try:
        app = App(qapp, paths, args)
    except Exception as e:
        traceback.print_exc()
        QtWidgets.QMessageBox.critical(None, "Desktop Pets - startup failed",
                                       f"{type(e).__name__}: {e}\n\nSee the console for details.\n"
                                       "Make sure your GPU drivers support OpenGL 3.3.")
        return 1
    code = qapp.exec()
    app.watcher.stop()                       # the polling thread is a daemon,
    return code                              # but stop it properly anyway


if __name__ == "__main__":
    sys.exit(main())
