#!/usr/bin/env python3
"""calamitas_pet.py -- Supreme Calamitas (pre-brothers AI) as a Win32 colour-key desktop pet.

PROVENANCE NOTES (the file carries its own write-up)
* Line numbers: the attached copies carry no numbering. Every `File.cs:~N` is an estimate,
  trust the identifier / #region named next to it, not N.
* Couplings resolved to constants:
    wormAlive=False, cataclysmAlive=False, catastropheAlive=False, permafrost=False,
    zenithWorld=False, getGoodWorld=False, uDieLul=1.0, protectionBoost=False,
    attackPause=0, lifeRatio=1.0 (=> chargeVelocity 30), difficulty=Revengeance (invented choice).
* Sepulcher / BrimstoneHeart: the source summons them right after BH1 (FirstAttack ->
  DoHeartsSpawningCastAnimation). The brief scopes the worm out, so they are excluded and the
  pet fights as she does AFTER the worm is dead (wormAlive=False).
* BrimstoneMonster: only spawned in BH4 (FourthAttack, lifeRatio <= 0.3) -> excluded.
* BH2/BH3 are HP-gated (lifeRatio <= 0.75 / 0.5); the pet has no HP -> only BH1 (unconditional) is ported.
* Soul Seekers: the brief says pre-brothers, but SupremeCalamitas.cs gates them on `IsAtSeekers`
  (lifeRatio <= 0.2), i.e. after the brothers. No sprite on disk -> stubbed, see _spawn_soul_seekers.
* The 04_BlastPunchCast and 06_Clap poses are ONLY drawn by the LastStage (ai[0]==3) branch, which the
  brief's gloss calls out of scope; the 7-pose acceptance test needs them, so the *_p2 attacks (LastStage
  timings of the same hellblast/gigablast/hover/charge attacks) are included. Permafrost is NOT included.
* BrimstoneWave is never fired pre-brothers in a non-zenith world (non-zenith: only BH5). The only
  pre-brothers path is the zenith swap `hellblast = zenithAI ? BrimstoneWave : BrimstoneHellblast`
  (phase 3), so phase 3 alternates the two projectiles (alternation invented -- test needs 'wave').
* winsound: CPython rejects SND_MEMORY|SND_ASYNC with RuntimeError; Audio tries it first as the brief
  asks and falls back to a worker thread playing SND_MEMORY synchronously.
* Colour key: pure green (0,255,0) verified absent from sprites by the brief.
* Forcefield shader, dust, sparks, bloom textures: no alpha with a colour key -> omitted/approximated.
"""
import ctypes
import io
import math
import os
import random
import re
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import winsound
except ImportError:  # non-Windows (headless tests of blit etc.)
    winsound = None

# ----------------------------------------------------------------------------------------------
# Public constants
# ----------------------------------------------------------------------------------------------
SRCCOPY = 0x00CC0020        # Win32 raster op, used by the tests to read the desktop back
SCALE = 2                   # invented -- presentation only
FPS = 60                    # invented -- presentation only (matches Terraria's 60 UPS)

HERE = Path(__file__).resolve().parent
SPR = HERE / "assets" / "sprites"
SFX = HERE / "assets" / "sfx"
FRM = HERE / "assets" / "frames"

# SupremeCalamitas.cs:~478 FrameChangeSpeed=0.15f, ~2680 0.175f, ~3210 0.245f; ms = 1000/(60*speed)
ANIM_MS = {
    "00_UpwardDraft": 111,
    "01_UpwardDraftTall": 111,
    "02_Casting": 111,
    "03_BothArmsRaise": 95,
    "04_BlastPunchCast": 100,
    "05_LeftHandshake": 111,
    "06_Clap": 68,
}
_ANIM_ORDER = list(ANIM_MS)
_IDLE = "00_UpwardDraft"
_ONE_SHOT = "04_BlastPunchCast"

# Main.projFrames[Type] in each SetStaticDefaults (Barrage.cs:~14, Wave.cs:~19, Hellblast.cs:~17,
# Hellblast2.cs:~17, SCalBrimstoneFireblast.cs:~30, SCalBrimstoneGigablast.cs:~30)
PROJ_FRAMES = {
    "BrimstoneBarrage": 4,
    "BrimstoneWave": 4,
    "BrimstoneHellblast": 4,
    "BrimstoneHellblast2": 4,
    "SCalBrimstoneFireblast": 5,
    "SCalBrimstoneGigablast": 6,
}

# ----------------------------------------------------------------------------------------------
# Source constants (private)
# ----------------------------------------------------------------------------------------------
_TPS = 60.0                  # Terraria fixed update rate -- engine constant, not in the attached files
_GAP = 1.0 / 60.0            # SupremeCalamitas.cs:~2190 `if (NPC.ai[1] == -1f)` selection tick: sets ai[1]=phase, attack branch NOT run -> 1 idle tick
_UDIELUL = 1.0               # SupremeCalamitas.cs:~890 uDieLul stays 1 while the target is inside the arena (pet has no arena)
_BH_GATE = 8                 # SupremeCalamitas.cs:~520 baseBulletHellProjectileGateValue = revenge ? 8 : expertMode ? 9 : 10 (revenge chosen: invented)
_HOVER_ABOVE = 550.0         # SupremeCalamitas.cs:~2335 new Vector2(player.Center.X, player.Center.Y - 550f)
_HOVER_VEL = 12.0            # SupremeCalamitas.cs:~2325 float velocity = 12f
_HOVER_ACC = 0.12            # SupremeCalamitas.cs:~2326 float acceleration = 0.12f
_SIDE_VEL = 32.0             # SupremeCalamitas.cs:~2600 (phase 3) and ~2675 (phase 4) float velocity = 32f
_SIDE_ACC = 1.2              # SupremeCalamitas.cs:~2601 float acceleration = 1.2f
_SIDE_P3 = 600.0             # SupremeCalamitas.cs:~2610 player.Center.X + posX * 600f
_SIDE_P4 = 750.0             # SupremeCalamitas.cs:~2685 player.Center.X + posX * 750f
_DASH_DAMP_FROM = 25         # SupremeCalamitas.cs:~2545 `if (NPC.ai[2] >= 25f)` -> velocity *= 0.96f
_DASH_DAMP = 0.96            # SupremeCalamitas.cs:~2548
_DASH_STOP = 0.1             # SupremeCalamitas.cs:~2551 `velocity.X > -0.1 && < 0.1` -> 0
_FACE_DEADZONE = 16.0        # SupremeCalamitas.cs:~600 `Math.Abs(player.Center.X - NPC.Center.X) > 16f`
_FACE_DASH_MIN = 0.15        # SupremeCalamitas.cs:~2538 `Math.Abs(NPC.velocity.X) > 0.15f`
_BH_DAMP = 0.95              # SupremeCalamitas.cs:~1062 `NPC.velocity *= 0.95f` during BH1
_MUZZLE_P0 = 8.0             # SupremeCalamitas.cs:~2345 projectileSpawn = NPC.Center + projectileVelocity * 8f
_MUZZLE_P3 = 4.0             # SupremeCalamitas.cs:~2625 projectileSpawn = NPC.Center + projectileVelocity * 4f
_MUZZLE_P4 = 8.0             # SupremeCalamitas.cs:~2700 projectileSpawn = NPC.Center + projectileVelocity * 8f
_SHIELD_FADE = 0.08          # SupremeCalamitas.cs:~680 MathHelper.Lerp(shieldOpacity, 1f/0f, 0.08f)
_SHIELD_LERP = 0.125         # SupremeCalamitas.cs:~665 shieldRotation.AngleLerp(idealRotation, 0.125f)
_SHIELD_TOWARDS = 0.18       # SupremeCalamitas.cs:~666 shieldRotation.AngleTowards(idealRotation, 0.18f)
_SHIELD_MIN_OFF = 0.04       # SupremeCalamitas.cs:~664 `if (angularOffset > 0.04f)`
_SHIELD_SOLID = 0.55         # SupremeCalamitas.cs:~3700 CanHitPlayer `shieldOpacity > 0.55f`
# Forcefield. SupremeCalamitas.cs:546-620 "Forcefield and Shield Logic" drives the visible
# sphere through forcefieldScale, not forcefieldOpacity: scale lerps to 0.45 while the shield is
# up and back to 1 otherwise, and only hits 0 in the acceptance/death phase. forcefieldOpacity
# itself stays at its 1f default (line ~171) for the whole normal fight -- it is only lowered at
# BH4 (1304, -> 0.4) and post-music-hit (1325, -> 0.7), neither of which the pet reaches.
# So the sphere is always drawn, and it visibly shrinks during a charge.
_FF_SHRINK = 0.45           # SupremeCalamitas.cs:612 `forcefieldScale = Lerp(forcefieldScale, 0.45f, 0.08f)`
_FF_LERP = 0.08             # SupremeCalamitas.cs:612 the same 0.08f lerp factor as shieldOpacity
_FF_VS_SPRITE = 1.55        # invented -- sphere quad height as a multiple of her sprite height.
# The C# quad is 216px against a 152.8px hitbox (1.414x), but the pet has no hitbox to measure.
# 1.55x her sprite height reads as a bubble enclosing her at SCALE=2. Scales with SCALE.
_SHIELD_FWD = 24.0           # SupremeCalamitas.cs:~3795 DrawShield `shieldRotation.ToRotationVector2() * 24f`
_JAW_OFF = 42.0              # SupremeCalamitas.cs:~3800 DrawShield `* 42f`
_JAW_DASH = -0.71            # SupremeCalamitas.cs:~3783 jawRotationOffset -= 0.71f while ai[1]==2
_JAW_LAUGH = (0.04, -0.82, 17.2)  # SupremeCalamitas.cs:~3787 Lerp(0.04f, -0.82f, sin(t*17.2f)*0.5+0.5)
_CLOSE_MARGIN = 30           # SupremeCalamitas.cs:~232 AttackCloseToBeingOver `ai[2] >= attackLength - 30f`
_WILL_CHARGE_TINT = 0.3      # SupremeCalamitas.cs:~3690 GetAlpha Color.Lerp(Color.Red, drawColor, 0.7f) -> 0.3 red

# SupremeCalamitas.cs:~2190 (#region FirstStage, switch (phaseChange) cases 0..23; identical copy in LastStage ~2830)
_PHASE_TABLE = [0, 3, 4, 1, 1, 4, 3, 0, 1, 0, 3, 4, 4, 3, 1, 0, 4, 4, 1, 1, 0, 1, 0, 1]
# same switch: the cases that assign willCharge
_WILL_CHARGE_AT = {0: False, 2: True, 5: False, 7: True, 9: False, 13: True, 15: False, 17: True}
# SCOPE TOGGLE.  04_BlastPunchCast and 06_Clap are ONLY drawn by the LastStage
# (ai[0]==3) branch, which is post-brothers and therefore out of the requested
# pre-brothers scope. Left ON so all 7 sprite animations get exercised; set to
# False for strict pre-brothers behaviour, after which 04/06 never play.
INCLUDE_POST_BROTHERS_STAGE = True
_STAGE2_FROM = 8 if INCLUDE_POST_BROTHERS_STAGE else (len(_PHASE_TABLE) + 1)
# SupremeCalamitas.cs:~205 AttackCloseToBeingOver (ai[0]==0 branch) + exits `ai[2] >= N` (p0 ~2352, p3 ~2615, p4 ~2715)
_P1_LEN = {0: 300, 3: 480, 4: 300}
# SupremeCalamitas.cs:~212 AttackCloseToBeingOver (else branch) + LastStage exits (~3010, ~3195, ~3290)
_P2_LEN = {0: 240, 3: 300, 4: 240}

# Projectile rules.  life = Projectile.timeLeft in SetDefaults; fth = frameCounter threshold; ups = extraUpdates+1
_PROJ_RULES = {
    # BrimstoneBarrage.cs:~33 timeLeft=690; ~70 `frameCounter > 4`
    "BrimstoneBarrage": dict(life=690, fth=4, ups=1, mode="down", op0=1.0),
    # BrimstoneWave.cs:~31 timeLeft=1200; ~47 `frameCounter > 12`
    "BrimstoneWave": dict(life=1200, fth=12, ups=1, mode="wave", op0=0.0),
    # BrimstoneHellblast.cs:~33 timeLeft=255; ~46 `frameCounter >= 10`
    "BrimstoneHellblast": dict(life=255, fth=9, ups=1, mode="hell", op0=1.0),
    # BrimstoneHellblast2.cs:~30 extraUpdates=1, ~32 timeLeft=1500; ~44 `frameCounter >= 10`
    "BrimstoneHellblast2": dict(life=1500, fth=9, ups=2, mode="hell", op0=0.0),
    # SCalBrimstoneFireblast.cs:~47 timeLeft=150; ~58 `frameCounter > 4`
    "SCalBrimstoneFireblast": dict(life=150, fth=4, ups=1, mode="down", op0=0.0),
    # SCalBrimstoneGigablast.cs:~47 timeLeft=120; ~60 `frameCounter > 4`
    "SCalBrimstoneGigablast": dict(life=120, fth=4, ups=1, mode="down", op0=0.0),
}
_FIRE_INERTIA = 80.0         # SCalBrimstoneFireblast.cs:~85 inertia = revenge ? 80f : 100f
_FIRE_HOME = 13.0            # SCalBrimstoneFireblast.cs:~86 homeSpeed = revenge ? 13f : 9f
_FIRE_MIN_DIST = 40.0        # SCalBrimstoneFireblast.cs:~87 minDist = 40f
_DETONATE_RANGE = 224.0      # Fireblast.cs:~122 / Gigablast.cs:~117 `targetDist < 224` (14 blocks)
_DETONATE_TICKS = 60         # Fireblast.cs:~125 / Gigablast.cs:~119 `Projectile.timeLeft = 60`
# OnKill ring: Fireblast.cs:~192 `death ? 16 : revenge ? 14 : ...` speed 8f; Gigablast.cs:~188 `death ? 36 : revenge ? 32 : ...` speed 6.5f
_RING = {"SCalBrimstoneFireblast": (14, 8.0), "SCalBrimstoneGigablast": (32, 6.5)}
_RING_CAP_MULT = 1.5         # Fireblast.cs:~196 / Gigablast.cs:~192 `velocity * 1.5f` passed as ai[2]
# Fireblast.cs:~20 ImpactSound / Gigablast.cs:~20 ImpactSound, played first thing in OnKill
_IMPACT_SFX = {"SCalBrimstoneFireblast": "BrimstoneFireblastImpact",
               "SCalBrimstoneGigablast": "BrimstoneGigablastImpact"}
# BloomParticle radius is not in the attached files (only scale 0.7/0.85 + lifetimes) -> invented, presentation only
_BLOOM_R = {"SCalBrimstoneFireblast": 20, "SCalBrimstoneGigablast": 28}


def _t(ticks):
    """ticks -> seconds."""
    return ticks / _TPS


# ----------------------------------------------------------------------------------------------
# ATTACKS.  speed is in source px per projectile update (not scaled), time offsets/dur in seconds.
# Extra keys beyond the contract: phase (ai[1] value), stage, muzzle, fan, cap_mult, dash_at, sfx_at,
# anim_reset_at, stream.
# ----------------------------------------------------------------------------------------------
ATTACKS = [
    # ---------------- opener: first bullet hell (#region FirstAttack, ~1035) ----------------
    dict(
        key="bullethell",
        # no ai[1] value: it is the `!FinishedBH1` branch
        phase=-1,
        stage=1,
        # SupremeCalamitas.cs:~1128 `FrameType = FrameAnimationType.Casting;` in the BH1 branch
        anim="02_Casting",
        # SupremeCalamitas.cs:~2190 one-tick selection gap
        cd=_GAP,
        # SupremeCalamitas.cs:~90 `BulletHellDuration = 900`
        dur=_t(900),
        # SupremeCalamitas.cs:~1050 `bulletHellCounter2 == BulletHellDuration - 360` -> BulletHellSound (SCalRumble); counter is pre-incremented -> tick index 539
        sfx="SCalRumble",
        sfx_at=[_t(539)],
        # projectiles are random + cursor-relative, produced by Calamitas._bh1_stream
        shots=[],
        stream="bh1",
    ),
    # ---------------- stage 1, phase 0 slots (ai[1]==0, hover above, ~2335) ----------------
    dict(
        key="barrage",
        phase=0,
        stage=1,
        # SupremeCalamitas.cs:~2395 `FrameType = FasterUpwardDraft` (-> folder 01_UpwardDraftTall)
        anim="01_UpwardDraftTall",
        cd=_GAP,
        # SupremeCalamitas.cs:~2347 `localAI[1] > 90f` -> period 91 ticks
        dur=_t(91),
        # SupremeCalamitas.cs:~2398 barrage branch `PlaySound(BrimstoneBigShotSound)` -> BrimstoneBigShoot
        sfx="BrimstoneBigShoot",
        # name, offset (fires on the 91st tick = after 90 elapsed), speed `projectileVelocity *= 10f * uDieLul` (~2344), homing 0 (ai[0]=0), spin 0 (no spinning projectile in source)
        shots=[("BrimstoneBarrage", _t(90), 10.0, 0.0, 0.0)],
        # SupremeCalamitas.cs:~2402 numProj = 8, rotation = ToRadians(20), Lerp(-rotation, rotation, j/(numProj-1))
        fan=(8, math.radians(20.0)),
        # SupremeCalamitas.cs:~2405 projectileVelocityToPass = projectileVelocity.Length() * 1.3f (ai[2] speed cap)
        cap_mult=1.3,
        muzzle=_MUZZLE_P0,
    ),
    dict(
        key="fireblast",
        phase=0,
        stage=1,
        anim="01_UpwardDraftTall",
        cd=_GAP,
        dur=_t(91),
        # SupremeCalamitas.cs:~2378 fireblast branch `PlaySound(BrimstoneShotSound)` -> BrimstoneShoot
        sfx="BrimstoneShoot",
        # speed 10 (~2344); homing 1.0 = projectile's own AI steers (Fireblast.cs:~85-101, ai[0]=0 -> player 0); spin 0
        shots=[("SCalBrimstoneFireblast", _t(90), 10.0, 1.0, 0.0)],
        muzzle=_MUZZLE_P0,
    ),
    dict(
        key="gigablast_hover",
        phase=0,
        stage=1,
        anim="01_UpwardDraftTall",
        cd=_GAP,
        dur=_t(91),
        # SupremeCalamitas.cs:~2358 gigablast branch `PlaySound(BrimstoneBigShotSound)` -> BrimstoneBigShoot
        sfx="BrimstoneBigShoot",
        # speed 10 (~2344); homing 1.0 = Gigablast.cs:~75-80 `(velocity*24 + playerVec)/25`; spin 0
        shots=[("SCalBrimstoneGigablast", _t(90), 10.0, 1.0, 0.0)],
        muzzle=_MUZZLE_P0,
    ),
    # ---------------- stage 1, phase 3 (hellblasts, ~2595) ----------------
    dict(
        key="hellblast",
        phase=3,
        stage=1,
        # SupremeCalamitas.cs:~2655 `FrameType = OutwardHandCast` (-> 05_LeftHandshake)
        anim="05_LeftHandshake",
        cd=_GAP,
        # SupremeCalamitas.cs:~2615 `ai[2] >= 480f`
        dur=_t(480),
        # SupremeCalamitas.cs:~2622 `PlaySound(HellblastSound)` -> BrimstoneHellblastSound
        sfx="BrimstoneHellblastSound",
        # shots at ai[3] >= 20 (~2620): tick 20n-1 for n=1..23 (n=24 would land on the exit tick); speed 10f * uDieLul (~2627); homing 0, spin 0
        shots=[("BrimstoneHellblast", _t(20 * n - 1), 10.0, 0.0, 0.0) for n in range(1, 24)],
        muzzle=_MUZZLE_P3,
    ),
    dict(
        key="wave",
        phase=3,
        stage=1,
        anim="05_LeftHandshake",
        cd=_GAP,
        dur=_t(480),
        sfx="BrimstoneHellblastSound",
        # zenith swap `int hellblast = zenithAI ? BrimstoneWave : BrimstoneHellblast` (~507); same cadence/speed as hellblast
        shots=[("BrimstoneWave", _t(20 * n - 1), 10.0, 0.0, 0.0) for n in range(1, 24)],
        muzzle=_MUZZLE_P3,
    ),
    # ---------------- stage 1, phase 4 (gigablasts, ~2665) ----------------
    dict(
        key="gigablast",
        phase=4,
        stage=1,
        # SupremeCalamitas.cs:~2685 `FrameType = BlastCast` (-> 03_BothArmsRaise)
        anim="03_BothArmsRaise",
        cd=_GAP,
        # SupremeCalamitas.cs:~2715 `ai[2] >= 300f`
        dur=_t(300),
        # SupremeCalamitas.cs:~2698 `PlaySound(BrimstoneBigShotSound)` -> BrimstoneBigShoot
        sfx="BrimstoneBigShoot",
        # shootRate = wormAlive ? 280 : 140 (~2682); wormAlive resolved False (Sepulcher out of scope, already dead) -> 140; fires on localAI[1] > 140 -> ticks 140 and 281 (period 141); speed 5f * uDieLul (~2702); homing 1.0; spin 0
        shots=[("SCalBrimstoneGigablast", _t(140), 5.0, 1.0, 0.0),
               ("SCalBrimstoneGigablast", _t(281), 5.0, 1.0, 0.0)],
        muzzle=_MUZZLE_P4,
    ),
    # ---------------- stage 1, phase 1 (charge x2, ~2470) ----------------
    dict(
        key="bash",
        phase=1,
        stage=1,
        # SupremeCalamitas.cs:~2552 `FrameType = FasterUpwardDraft` during ai[1]==2
        anim="01_UpwardDraftTall",
        cd=_GAP,
        # two dashes: ai[2] 1..70 (~2556), second dash tick 71, exit tick 141 -> 142 ticks
        dur=_t(142),
        # SupremeCalamitas.cs:~2490 chargeVelocity = (wormAlive ? 26f : 30f) + (1f - lifeRatio) * 8f ; wormAlive False, lifeRatio 1 -> 30
        dash=30.0,
        dash_at=[_t(0), _t(71)],
        # SupremeCalamitas.cs:~2497 `PlaySound(DashSound)` -> SCalDash, once per dash
        sfx="SCalDash",
        sfx_at=[_t(0), _t(71)],
        shots=[],
    ),
    # ---------------- stage 2 (LastStage timings, ~2800) ----------------
    dict(
        key="barrage_p2",
        phase=0,
        stage=2,
        anim="01_UpwardDraftTall",
        cd=_GAP,
        # SupremeCalamitas.cs:~3010 LastStage `localAI[1] > 60f` -> period 61 ticks
        dur=_t(61),
        sfx="BrimstoneBigShoot",
        shots=[("BrimstoneBarrage", _t(60), 10.0, 0.0, 0.0)],
        fan=(8, math.radians(20.0)),
        cap_mult=1.3,
        muzzle=_MUZZLE_P0,
    ),
    dict(
        key="fireblast_p2",
        phase=0,
        stage=2,
        anim="01_UpwardDraftTall",
        cd=_GAP,
        dur=_t(61),
        sfx="BrimstoneShoot",
        shots=[("SCalBrimstoneFireblast", _t(60), 10.0, 1.0, 0.0)],
        muzzle=_MUZZLE_P0,
    ),
    dict(
        key="gigablast_hover_p2",
        phase=0,
        stage=2,
        anim="01_UpwardDraftTall",
        cd=_GAP,
        dur=_t(61),
        sfx="BrimstoneBigShoot",
        shots=[("SCalBrimstoneGigablast", _t(60), 10.0, 1.0, 0.0)],
        muzzle=_MUZZLE_P0,
    ),
    dict(
        key="hellblast_p2",
        phase=3,
        stage=2,
        # SupremeCalamitas.cs:~3215 LastStage `FrameType = PunchHandCast` (-> 06_Clap)
        anim="06_Clap",
        cd=_GAP,
        # SupremeCalamitas.cs:~3195 LastStage `ai[2] >= 300f`
        dur=_t(300),
        sfx="BrimstoneHellblastSound",
        # LastStage `ai[3] >= 24f` (~3200): ticks 24n-1, n=1..12
        shots=[("BrimstoneHellblast", _t(24 * n - 1), 10.0, 0.0, 0.0) for n in range(1, 13)],
        muzzle=_MUZZLE_P3,
    ),
    dict(
        key="wave_p2",
        phase=3,
        stage=2,
        anim="06_Clap",
        cd=_GAP,
        dur=_t(300),
        sfx="BrimstoneHellblastSound",
        shots=[("BrimstoneWave", _t(24 * n - 1), 10.0, 0.0, 0.0) for n in range(1, 13)],
        muzzle=_MUZZLE_P3,
    ),
    dict(
        key="gigablast_p2",
        phase=4,
        stage=2,
        # SupremeCalamitas.cs:~3262 LastStage `FrameType = BlastPunchCast` (only in the windup window in source; held for the whole attack here: presentation)
        anim="04_BlastPunchCast",
        cd=_GAP,
        # SupremeCalamitas.cs:~3290 LastStage `ai[2] >= 240f`
        dur=_t(240),
        sfx="BrimstoneBigShoot",
        # shootRate = wormAlive ? 200 : 100 (~3258) -> 100 ; ticks 100 and 201 ; speed 5f * uDieLul (~3282)
        shots=[("SCalBrimstoneGigablast", _t(100), 5.0, 1.0, 0.0),
               ("SCalBrimstoneGigablast", _t(201), 5.0, 1.0, 0.0)],
        # FindFrame (~3560): windup lerp starts `shootRate - 18` ticks before each shot -> restart the one-shot anim then
        anim_reset_at=[_t(100 - 18), _t(201 - 18)],
        muzzle=_MUZZLE_P4,
    ),
    dict(
        key="bash_p2",
        phase=1,
        stage=2,
        anim="01_UpwardDraftTall",
        cd=_GAP,
        # LastStage `willChargeAgain = ai[3] + 1 < 1` (~3120) -> single dash: dash at 0, exit tick 70 -> 71 ticks
        dur=_t(71),
        dash=30.0,
        dash_at=[_t(0)],
        sfx="SCalDash",
        sfx_at=[_t(0)],
        shots=[],
    ),
]
_BY_KEY = {a["key"]: a for a in ATTACKS}


# ----------------------------------------------------------------------------------------------
# Forcefield sphere (presentation only -- no fight logic lives here)
# ----------------------------------------------------------------------------------------------
# calamitas_forcefield.py re-implements Effects/SupremeShieldShader.fx as per-pixel alpha over a
# premultiplied BGRA buffer. It is a separate module so the headless tests can exercise the blitter
# without pulling in a ~23k-line-of-HLSL port; if it is missing the pet simply runs without the
# sphere rather than failing to start.
try:
    import calamitas_forcefield as _FFMOD
except Exception:  # pragma: no cover - optional presentation dependency
    _FFMOD = None


def _make_forcefield(w, h):
    """One sphere per Calamitas. Deliberately not a shared singleton: `scale` and `quad` are
    mutable per-instance state, so two instances would clobber each other's geometry."""
    if _FFMOD is None:
        return None
    try:
        return _FFMOD.Forcefield(size=(w, h), scale=1.0, time=0.0)
    except Exception:  # pragma: no cover
        return None


# ----------------------------------------------------------------------------------------------
# Image helpers
# ----------------------------------------------------------------------------------------------
_NEAREST = getattr(getattr(Image, "Resampling", Image), "NEAREST")


def scaled(path, scale=SCALE):
    im = Image.open(path).convert("RGBA")
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), _NEAREST)
    return np.array(im, dtype=np.uint8)


def strip_frames(path, n_frames, scale=SCALE):
    im = Image.open(path).convert("RGBA")
    fh = im.height // n_frames
    out = []
    for i in range(n_frames):
        crop = im.crop((0, i * fh, im.width, (i + 1) * fh))
        if scale != 1:
            crop = crop.resize((crop.width * scale, crop.height * scale), _NEAREST)
        out.append(np.array(crop, dtype=np.uint8))
    return out


def blit(canvas, sprite, x, y):
    """Alpha-blend an RGBA sprite into a premultiplied BGRA canvas.

    PIL gives straight (non-premultiplied) alpha but ULW_ALPHA requires
    premultiplied, so scale the colour channels by alpha here. uint16
    intermediate because rgb*alpha reaches 65025; +127 before the >>8 so
    a=255 round-trips exactly.

    Fully transparent source pixels are left untouched so layered sprites
    composite over each other instead of erasing one another.
    """
    sh, sw = sprite.shape[0], sprite.shape[1]
    ch, cw = canvas.shape[0], canvas.shape[1]
    x = int(x)
    y = int(y)
    x0 = max(x, 0)
    y0 = max(y, 0)
    x1 = min(x + sw, cw)
    y1 = min(y + sh, ch)
    if x0 >= x1 or y0 >= y1:
        return
    sub = sprite[y0 - y:y1 - y, x0 - x:x1 - x]
    a = sub[:, :, 3]
    mask = a > 0
    if not mask.any():
        return
    # Divide by 255 (not >>8) so alpha=255 round-trips to exactly 255.
    pm = ((sub[:, :, :3].astype(np.uint16) * a[:, :, None].astype(np.uint16) + 127)
          // 255).astype(np.uint8)
    out = np.concatenate([pm[:, :, 2::-1], a[:, :, None]], axis=2)   # BGRA
    region = canvas[y0:y1, x0:x1]
    region[mask] = out[mask].astype(region.dtype)


def painted_pixels(canvas):
    if canvas.shape[2] == 4:
        return int(np.count_nonzero(canvas[:, :, 3]))
    return int(np.count_nonzero(canvas.any(axis=2)))


def write_debug_png(canvas, path):
    """Composite over a checkerboard. Canvas is premultiplied BGRA, so this is
    the same blend the compositor does: out = src_premult + bg * (1 - alpha)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    a = canvas[:, :, 3].astype(np.float32) if canvas.shape[2] == 4 else np.full(
        canvas.shape[:2], 255.0, dtype=np.float32)
    src = canvas[:, :, 2::-1][:, :, :3].astype(np.float32)          # BGRA -> RGB
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    checker = (((xx // 16 + yy // 16) % 2) * 40 + 30).astype(np.float32)
    bg = np.repeat(checker[:, :, None], 3, axis=2)
    out = src + bg * (1.0 - a[:, :, None] / 255.0)
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB").save(str(path))


_ORIENT_CACHE = {}


def _orient(arr, angle, flip_h=False, flip_v=False):
    """Flip then rotate clockwise (screen space) by `angle`, NEAREST, cached (6 degree steps)."""
    q = int(round(math.degrees(angle) / 6.0)) % 60 if angle else 0
    key = (id(arr), q, flip_h, flip_v)
    hit = _ORIENT_CACHE.get(key)
    if hit is not None:
        return hit[1]
    img = arr
    if flip_h:
        img = img[:, ::-1]
    if flip_v:
        img = img[::-1]
    img = np.ascontiguousarray(img)
    if q:
        im = Image.fromarray(img, "RGBA").rotate(-q * 6.0, resample=_NEAREST, expand=True)
        img = np.ascontiguousarray(np.asarray(im))
    _ORIENT_CACHE[key] = (arr, img)  # keep `arr` alive so id() cannot be reused
    return img


def _disc(canvas, cx, cy, r, bgr):
    r = int(r)
    if r <= 0:
        return
    h, w = canvas.shape[:2]
    x0 = max(int(cx) - r, 0)
    x1 = min(int(cx) + r + 1, w)
    y0 = max(int(cy) - r, 0)
    y1 = min(int(cy) + r + 1, h)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.ogrid[y0:y1, x0:x1]
    mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
    region = canvas[y0:y1, x0:x1]
    col = np.asarray(bgr, dtype=region.dtype)
    if region.shape[2] == 4:
        # 4th channel is alpha; the disc is fully opaque so no premultiply needed
        col = np.concatenate([col, np.full(1, 255, region.dtype)])
    region[mask] = col


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def _angle_lerp(a, b, t):
    return a + _wrap(b - a) * t


def _angle_towards(a, b, step):
    d = _wrap(b - a)
    if abs(d) <= step:
        return b
    return a + math.copysign(step, d)


def _natural_key(p):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", p.name)]


# ----------------------------------------------------------------------------------------------
# Win32 binding
# ----------------------------------------------------------------------------------------------
class _W:
    ready = False


def _bind_win32():
    if _W.ready:
        return
    if sys.platform != "win32":
        raise OSError("LayeredWindow needs Windows")
    from ctypes import wintypes
    c = ctypes
    ptr = c.c_void_p
    LRESULT, WPARAM, LPARAM = c.c_ssize_t, c.c_size_t, c.c_ssize_t
    _W.WNDPROC = c.WINFUNCTYPE(LRESULT, ptr, c.c_uint, WPARAM, LPARAM)

    class WNDCLASSEXW(c.Structure):
        _fields_ = [("cbSize", c.c_uint), ("style", c.c_uint), ("lpfnWndProc", _W.WNDPROC),
                    ("cbClsExtra", c.c_int), ("cbWndExtra", c.c_int), ("hInstance", ptr),
                    ("hIcon", ptr), ("hCursor", ptr), ("hbrBackground", ptr),
                    ("lpszMenuName", c.c_wchar_p), ("lpszClassName", c.c_wchar_p), ("hIconSm", ptr)]

    class BITMAPINFOHEADER(c.Structure):
        _fields_ = [("biSize", c.c_uint32), ("biWidth", c.c_int32), ("biHeight", c.c_int32),
                    ("biPlanes", c.c_uint16), ("biBitCount", c.c_uint16), ("biCompression", c.c_uint32),
                    ("biSizeImage", c.c_uint32), ("biXPelsPerMeter", c.c_int32),
                    ("biYPelsPerMeter", c.c_int32), ("biClrUsed", c.c_uint32),
                    ("biClrImportant", c.c_uint32)]

    class BITMAPINFO(c.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", c.c_uint32 * 1)]

    class BLENDFUNCTION(c.Structure):
        _fields_ = [("BlendOp", c.c_ubyte), ("BlendFlags", c.c_ubyte),
                    ("SourceConstantAlpha", c.c_ubyte), ("AlphaFormat", c.c_ubyte)]

    _W.WNDCLASSEXW, _W.BITMAPINFOHEADER, _W.BITMAPINFO = WNDCLASSEXW, BITMAPINFOHEADER, BITMAPINFO
    _W.BLENDFUNCTION = BLENDFUNCTION
    _W.MSG, _W.POINT = wintypes.MSG, wintypes.POINT
    _W.SIZE = wintypes.SIZE

    u = _W.user32 = c.WinDLL("user32", use_last_error=True)
    g = _W.gdi32 = c.WinDLL("gdi32", use_last_error=True)
    k = _W.kernel32 = c.WinDLL("kernel32", use_last_error=True)

    def sig(fn, res, *args):
        fn.restype = res
        fn.argtypes = list(args)

    sig(u.RegisterClassExW, c.c_ushort, c.POINTER(WNDCLASSEXW))
    sig(u.UnregisterClassW, c.c_int, c.c_wchar_p, ptr)
    sig(u.CreateWindowExW, ptr, c.c_uint32, c.c_wchar_p, c.c_wchar_p, c.c_uint32,
        c.c_int, c.c_int, c.c_int, c.c_int, ptr, ptr, ptr, ptr)
    sig(u.DefWindowProcW, LRESULT, ptr, c.c_uint, WPARAM, LPARAM)
    sig(u.ShowWindow, c.c_int, ptr, c.c_int)
    sig(u.SetWindowPos, c.c_int, ptr, ptr, c.c_int, c.c_int, c.c_int, c.c_int, c.c_uint)
    sig(u.SetLayeredWindowAttributes, c.c_int, ptr, c.c_uint32, c.c_ubyte, c.c_uint32)
    # pblend must be a real BLENDFUNCTION*; NULL fails with ERROR_GEN_FAILURE on
    # this build even though MSDN says it is ignored when ULW_ALPHA is set.
    sig(u.UpdateLayeredWindow, c.c_int, ptr, ptr, c.POINTER(wintypes.POINT),
        c.POINTER(wintypes.SIZE), ptr, c.POINTER(wintypes.POINT),
        c.c_uint32, c.POINTER(BLENDFUNCTION), c.c_uint32)
    sig(u.PeekMessageW, c.c_int, c.POINTER(wintypes.MSG), ptr, c.c_uint, c.c_uint, c.c_uint)
    sig(u.TranslateMessage, c.c_int, c.POINTER(wintypes.MSG))
    sig(u.DispatchMessageW, LRESULT, c.POINTER(wintypes.MSG))
    sig(u.GetDC, ptr, ptr)
    sig(u.ReleaseDC, c.c_int, ptr, ptr)
    sig(u.InvalidateRect, c.c_int, ptr, ptr, c.c_int)
    sig(u.ValidateRect, c.c_int, ptr, ptr)
    sig(u.UpdateWindow, c.c_int, ptr)
    sig(u.DestroyWindow, c.c_int, ptr)
    sig(u.GetCursorPos, c.c_int, c.POINTER(wintypes.POINT))
    sig(u.GetSystemMetrics, c.c_int, c.c_int)
    sig(u.GetAsyncKeyState, c.c_short, c.c_int)
    sig(u.IsWindowVisible, c.c_int, ptr)
    sig(u.GetWindowLongPtrW, c.c_ssize_t, ptr, c.c_int)
    sig(u.SetProcessDPIAware, c.c_int)
    # GetPixel / BitBlt / DIB live in gdi32; GetDC / FillRect / ReleaseDC live in user32
    sig(g.CreateCompatibleDC, ptr, ptr)
    sig(g.CreateDIBSection, ptr, ptr, c.POINTER(BITMAPINFO), c.c_uint, c.POINTER(ptr), ptr, c.c_uint32)
    sig(g.SelectObject, ptr, ptr, ptr)
    sig(g.BitBlt, c.c_int, ptr, c.c_int, c.c_int, c.c_int, c.c_int, ptr, c.c_int, c.c_int, c.c_uint32)
    sig(g.GdiFlush, c.c_int)
    sig(g.DeleteObject, c.c_int, ptr)
    sig(g.DeleteDC, c.c_int, ptr)
    sig(g.GetPixel, c.c_uint32, ptr, c.c_int, c.c_int)
    sig(k.GetModuleHandleW, ptr, c.c_wchar_p)
    _W.ready = True


class LayeredWindow:
    """Full-screen, click-through, topmost per-pixel-alpha window.

    Transparency is UpdateLayeredWindow with ULW_ALPHA over a premultiplied
    BGRA DIB. Two things are load-bearing: pblend must be non-NULL, and
    SetLayeredWindowAttributes must never be called on this window (it puts it
    in colour-key mode, after which every ULW call fails).
    """

    def __init__(self):
        _bind_win32()
        W = _W
        c = ctypes
        self._closed = False
        self.alive = True
        self._ulw_ok = 0     # present() successes (BitBlt)
        self._ulw_fail = 0   # present() failures
        self._ulw_err = 0    # first error code seen
        try:
            W.user32.SetProcessDPIAware()
        except Exception:
            pass
        self.w = int(W.user32.GetSystemMetrics(0))
        self.h = int(W.user32.GetSystemMetrics(1))
        self._hinst = W.kernel32.GetModuleHandleW(None)
        self._cls = "CalamitasPet_%d_%d" % (os.getpid(), id(self) & 0xFFFF)
        self._proc = W.WNDPROC(self._wndproc)
        wc = W.WNDCLASSEXW()
        wc.cbSize = c.sizeof(W.WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = self._hinst
        wc.lpszClassName = self._cls
        if not W.user32.RegisterClassExW(c.byref(wc)):
            raise OSError("RegisterClassExW failed: %d" % c.get_last_error())
        # WS_EX_LAYERED | TOPMOST | TRANSPARENT | TOOLWINDOW | NOACTIVATE
        ex = 0x00080000 | 0x00000008 | 0x00000020 | 0x00000080 | 0x08000000
        self.hwnd = W.user32.CreateWindowExW(ex, self._cls, "Calamitas", 0x80000000,
                                             0, 0, self.w, self.h, None, None, self._hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW failed: %d" % c.get_last_error())
        # NOTE: do not call SetLayeredWindowAttributes here. It switches the
        # window to colour-key/constant-alpha mode, after which every
        # UpdateLayeredWindow call fails with ERROR_INVALID_PARAMETER.

        scr = W.user32.GetDC(None)
        self._hdc_scr = scr
        self._memdc = W.gdi32.CreateCompatibleDC(scr)
        bmi = W.BITMAPINFO()
        bmi.bmiHeader.biSize = c.sizeof(W.BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.w
        bmi.bmiHeader.biHeight = -self.h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0   # BI_RGB
        bits = c.c_void_p()
        self._hbmp = W.gdi32.CreateDIBSection(scr, c.byref(bmi), 0, c.byref(bits), None, 0)
        # the screen DC is kept: UpdateLayeredWindow needs it every frame
        if not self._hbmp or not bits.value:
            raise OSError("CreateDIBSection failed: %d" % c.get_last_error())
        self._old = W.gdi32.SelectObject(self._memdc, self._hbmp)
        self._bits = bits.value
        size = self.w * self.h * 4
        self._raw = (c.c_ubyte * size).from_address(self._bits)
        self._dib = np.frombuffer(self._raw, dtype=np.uint8).reshape(self.h, self.w, 4)
        # Premultiplied BGRA, straight onto the DIB (zero-copy). The alpha
        # channel is now live data rather than padding, so present() can hand
        # the whole thing to ULW_ALPHA.
        self.buf = self._dib
        self.clear()

        # pblend must be non-NULL: on this build ULW_ALPHA + NULL fails every
        # call with ERROR_GEN_FAILURE, despite MSDN saying it is ignored.
        self._blend = W.BLENDFUNCTION(0x00, 0x00, 0xFF, 0x01)  # SRC_OVER, SRC_ALPHA
        self._pt = ctypes.POINTER(_W.POINT)()
        self._pt.contents = _W.POINT(0, 0)
        self._size = _W.SIZE(self.w, self.h)

        # A window must be shown explicitly (WS_POPUP is not WS_VISIBLE).
        W.user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
        W.user32.SetWindowPos(self.hwnd, c.c_void_p(-1), 0, 0, self.w, self.h, 0x0010 | 0x0040)

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == 0x0084:      # WM_NCHITTEST -> HTTRANSPARENT
            return -1
        if msg == 0x0014:      # WM_ERASEBKGND
            return 1
        if msg == 0x000F:      # WM_PAINT: just validate, present() blits directly
            _W.user32.ValidateRect(hwnd, None)
            return 0
        if msg == 0x0002:      # WM_DESTROY
            self.alive = False
        return _W.user32.DefWindowProcW(hwnd, msg, wp, lp)

    def clear(self):
        self._dib[:] = 0        # alpha 0 == fully transparent

    def present(self):
        """Hand the premultiplied BGRA buffer to DWM for per-pixel-alpha output."""
        W = _W
        ok = W.user32.UpdateLayeredWindow(
            self.hwnd, self._hdc_scr, self._pt, ctypes.byref(self._size),
            self._memdc, self._pt, 0, ctypes.byref(self._blend), 0x02)  # ULW_ALPHA
        if ok:
            self._ulw_ok += 1
        else:
            self._ulw_fail += 1
            if not self._ulw_err:
                self._ulw_err = ctypes.get_last_error()
        return bool(ok)

    def pump(self):
        W = _W
        msg = W.MSG()
        while W.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
            if msg.message == 0x0012:  # WM_QUIT
                self.alive = False
            W.user32.TranslateMessage(ctypes.byref(msg))
            W.user32.DispatchMessageW(ctypes.byref(msg))
        return self.alive

    def cursor(self):
        pt = _W.POINT()
        _W.user32.GetCursorPos(ctypes.byref(pt))
        return (int(pt.x), int(pt.y))

    def esc_pressed(self):
        return bool(_W.user32.GetAsyncKeyState(0x1B) & 0x8000)

    def diagnostics(self):
        W = _W
        d = {"hwnd": self.hwnd, "size": (self.w, self.h),
             "transparency": "per-pixel alpha (UpdateLayeredWindow)",
             "ulw_ok": self._ulw_ok, "ulw_fail": self._ulw_fail, "ulw_err": self._ulw_err,
             "dib_bits": hex(self._bits or 0), "alive": self.alive}
        try:
            d["visible"] = bool(W.user32.IsWindowVisible(self.hwnd))
            d["exstyle"] = hex(W.user32.GetWindowLongPtrW(self.hwnd, -20) & 0xFFFFFFFF)
        except Exception as exc:
            d["diag_error"] = repr(exc)
        return d

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.alive = False
        W = _W
        self.buf = np.zeros((1, 1, 4), np.uint8)  # drop views onto the DIB before freeing it
        self._dib = self._raw = None
        try:
            W.user32.DestroyWindow(self.hwnd)
            W.gdi32.SelectObject(self._memdc, self._old)
            W.gdi32.DeleteObject(self._hbmp)
            W.gdi32.DeleteDC(self._memdc)
            W.user32.ReleaseDC(None, self._hdc_scr)
            W.user32.UnregisterClassW(self._cls, self._hinst)
        except Exception:
            pass


# ----------------------------------------------------------------------------------------------
# Audio
# ----------------------------------------------------------------------------------------------
class Audio:
    MASTER = 0.8   # invented -- presentation only
    MIN_GAP = 0.03  # invented -- presentation only (don't chop a sound with itself)

    def __init__(self):
        self.enabled = winsound is not None
        self._raw = {}
        self._cache = {}
        self._last = {}
        self._async_ok = True
        self._pending = None
        self._cv = threading.Condition()
        self._thread = None
        self._closed = False
        try:
            for p in sorted(SFX.glob("*.wav")):
                self._raw[p.stem] = p.read_bytes()
        except OSError:
            pass

    @staticmethod
    def _scale(raw, v):
        try:
            with wave.open(io.BytesIO(raw), "rb") as r:
                prm = r.getparams()
                frames = r.readframes(r.getnframes())
            if prm.sampwidth != 2:
                return raw
            arr = np.frombuffer(frames, dtype="<i2").astype(np.float32) * v
            out = np.clip(arr, -32768, 32767).astype("<i2").tobytes()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(prm.nchannels)
                w.setsampwidth(2)
                w.setframerate(prm.framerate)
                w.writeframes(out)
            return buf.getvalue()
        except Exception:
            return raw

    def _data(self, name, volume):
        raw = self._raw.get(name)
        if raw is None:
            return None
        v = 1.0 if volume is None else float(volume)
        v = max(0.0, min(1.0, v * self.MASTER))
        key = (name, round(v, 2))
        data = self._cache.get(key)
        if data is None:
            data = self._scale(raw, v)
            self._cache[key] = data
        return data

    def play(self, name, volume=None):
        if not self.enabled or self._closed:
            return False
        now = time.perf_counter()
        if now - self._last.get(name, -1.0) < self.MIN_GAP:
            return False
        data = self._data(name, volume)
        if data is None:
            return False
        self._last[name] = now
        if self._async_ok:
            try:
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                return True
            except RuntimeError:   # "Cannot play asynchronously from memory"
                self._async_ok = False
            except Exception:
                return False
        with self._cv:
            self._pending = data
            if self._thread is None:
                self._thread = threading.Thread(target=self._worker, daemon=True)
                self._thread.start()
            self._cv.notify()
        return True

    def _worker(self):
        while True:
            with self._cv:
                while self._pending is None and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
                data, self._pending = self._pending, None
            try:
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
            except Exception:
                pass

    def close(self):
        self._closed = True
        with self._cv:
            self._cv.notify_all()
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass


# ----------------------------------------------------------------------------------------------
# Animation
# ----------------------------------------------------------------------------------------------
class Anim:
    def __init__(self, folder_name, ms, loop=True):
        paths = sorted(FRM.glob("SupremeCalamitasHooded/%s/*.png" % folder_name), key=_natural_key)
        if not paths:
            paths = sorted(FRM.glob("*/%s/*.png" % folder_name), key=_natural_key)
        if not paths:
            raise FileNotFoundError("no frames for %r under %s" % (folder_name, FRM))
        self.name = folder_name
        self.ms = float(ms)
        self.loop = bool(loop)
        self.frames = [scaled(p) for p in paths]
        self.flipped = [np.ascontiguousarray(f[:, ::-1]) for f in self.frames]
        self.n = len(self.frames)
        self.i = 0
        self._t = 0.0

    def reset(self):
        self.i = 0
        self._t = 0.0

    def update(self, dt):
        self._t += max(0.0, dt) * 1000.0
        while self._t >= self.ms:
            self._t -= self.ms
            if self.loop:
                self.i = (self.i + 1) % self.n
            else:
                self.i = min(self.i + 1, self.n - 1)  # one-shot: hold final frame

    def frame(self, flip=False):
        return (self.flipped if flip else self.frames)[self.i]


# ----------------------------------------------------------------------------------------------
# Projectile
# ----------------------------------------------------------------------------------------------
class Projectile:
    """One hostile projectile. Speeds/lengths arrive already scaled by the owner's world scale `k`."""

    def __init__(self, name, frames, x, y, vx, vy, k=1.0, homing=0.0, spin=0.0, cap=None, ai1=2.0):
        rule = _PROJ_RULES[name]
        self.name = name
        self.frames = frames
        self.x, self.y = float(x), float(y)
        self.vx, self.vy = float(vx), float(vy)
        self.k = float(k)
        self.homing = float(homing)
        self.spin = float(spin)
        self.ai1 = float(ai1)
        self.life = rule["life"]
        self.fth = rule["fth"]
        self.ups = rule["ups"]
        self.mode = rule["mode"]
        self.opacity = rule["op0"]
        self.cap = float(cap) if cap is not None else math.hypot(self.vx, self.vy)
        self.frame = 0
        self.fcount = 0
        self.time = 0
        self.within = False
        self.set_life = False
        self.alive = True
        self._wave_t = 0

    def step(self, cursor, owner):
        for _ in range(self.ups):
            if not self.alive:
                break
            self._update(cursor, owner)

    def _update(self, cur, owner):
        k = self.k
        n = self.name
        self.time += 1
        self.fcount += 1
        if self.fcount > self.fth:
            self.frame = (self.frame + 1) % len(self.frames)
            self.fcount = 0
        life = self.life
        if n == "BrimstoneBarrage":
            # BrimstoneBarrage.cs:~55 `if (velocity.Length() < ai[2]) velocity *= 1.01f` clamped to ai[2]
            if math.hypot(self.vx, self.vy) < self.cap:
                self.vx *= 1.01
                self.vy *= 1.01
                m = math.hypot(self.vx, self.vy)
                if m > self.cap:
                    self.vx, self.vy = self.vx / m * self.cap, self.vy / m * self.cap
            # BrimstoneBarrage.cs:~85 ai[0]==2 homing for timeLeft > 570: (v*15 + dir)/16
            if self.homing and life > 570:
                dx, dy = cur[0] - self.x, cur[1] - self.y
                d = math.hypot(dx, dy)
                sp = math.hypot(self.vx, self.vy)
                if d > 0 and sp > 0:
                    self.vx = (self.vx * 15 + dx / d * sp) / 16
                    self.vy = (self.vy * 15 + dy / d * sp) / 16
                    m = math.hypot(self.vx, self.vy)
                    if m > 0:
                        self.vx, self.vy = self.vx / m * sp, self.vy / m * sp
            # BrimstoneBarrage.cs:~78 `timeLeft < 60` -> Opacity = timeLeft / 60
            if life < 60:
                self.opacity = max(0.0, life / 60.0)
        elif n == "BrimstoneWave":
            # BrimstoneWave.cs:~49 x++; velocity.Y = 5 * sin(x / 5)
            self._wave_t += 1
            self.vy = 5.0 * k * math.sin(self._wave_t / 5.0)
            if life < 60:   # BrimstoneWave.cs:~61
                self.opacity = max(0.0, min(1.0, life / 60.0))
            else:           # BrimstoneWave.cs:~63 1 - (timeLeft - 1140) / 30
                self.opacity = max(0.0, min(1.0, 1.0 - (life - 1140) / 30.0))
        elif n == "BrimstoneHellblast":
            # BrimstoneHellblast.cs:~85 `if (velocity.Length() < 18f) velocity *= 1.03f`
            if math.hypot(self.vx, self.vy) < 18.0 * k:
                self.vx *= 1.03
                self.vy *= 1.03
            # BrimstoneHellblast.cs:~78 `timeLeft < 51` -> Opacity -= 0.02f
            if life < 51:
                self.opacity = max(0.0, self.opacity - 0.02)
        elif n == "BrimstoneHellblast2":
            # BrimstoneHellblast2.cs:~59-62
            if life < 60:
                self.opacity = max(0.0, min(1.0, life / 60.0))
            else:
                self.opacity = max(0.0, min(1.0, 1.0 - (life - 1440) / 60.0))
        else:  # fireblast / gigablast
            if not self.within:
                # Fireblast.cs:~69 / Gigablast.cs:~64 Opacity = Clamp(1 - (timeLeft - 130) / 20)
                self.opacity = max(0.0, min(1.0, 1.0 - (life - 130) / 20.0))
                if self.homing:
                    dx, dy = cur[0] - self.x, cur[1] - self.y
                    d = math.hypot(dx, dy)
                    if n == "SCalBrimstoneFireblast":
                        if d > _FIRE_MIN_DIST * k:
                            self.vx = (self.vx * (_FIRE_INERTIA - 1) + dx / d * _FIRE_HOME * k) / _FIRE_INERTIA
                            self.vy = (self.vy * (_FIRE_INERTIA - 1) + dy / d * _FIRE_HOME * k) / _FIRE_INERTIA
                    else:
                        sp = math.hypot(self.vx, self.vy)
                        if d > 0 and sp > 0:
                            self.vx = (self.vx * 24 + dx / d * sp) / 25
                            self.vy = (self.vy * 24 + dy / d * sp) / 25
                            m = math.hypot(self.vx, self.vy)
                            if m > 0:
                                self.vx, self.vy = self.vx / m * sp, self.vy / m * sp
            dist = math.hypot(cur[0] - self.x, cur[1] - self.y)
            if (life == 1 and not self.within) or (dist < _DETONATE_RANGE * k and self.opacity == 1.0):
                if not self.set_life:
                    self.life = _DETONATE_TICKS
                    life = self.life
                    self.set_life = True
                self.within = True
            if self.within:
                self.vx *= 0.9
                self.vy *= 0.9
                if life <= 40 and self.opacity > 0:
                    self.opacity = max(0.0, self.opacity - 0.05)
                if life == 30:
                    self.opacity = 0.0
                    self.vx = self.vy = 0.0
        if self.spin:
            c, s = math.cos(self.spin), math.sin(self.spin)
            self.vx, self.vy = self.vx * c - self.vy * s, self.vx * s + self.vy * c
        self.x += self.vx
        self.y += self.vy
        self.life -= 1
        if self.life <= 0:
            self.alive = False
            owner._on_projectile_death(self)

    def draw(self, canvas):
        if self.within and self.life <= 30 and self.name in _BLOOM_R:
            base = _BLOOM_R[self.name] * SCALE * (0.35 + 0.65 * min(1.0, self.life / 30.0))
            col = (77, 21, 121) if self.life > 15 else ((0, 0, 255) if self.life > 8 else (255, 255, 255))
            _disc(canvas, self.x, self.y, base, col)
        if self.opacity < 0.5:   # no alpha with a colour key: hide when mostly faded
            return
        frame = self.frames[self.frame % len(self.frames)]
        flip_h = False
        if self.mode == "down":      # rotation = velocity.ToRotation() + PiOver2
            ang = math.atan2(self.vy, self.vx) + math.pi / 2
        elif self.mode == "hell":    # Hellblast.cs:~97 spriteDirection/rotation
            if self.vx < 0:
                flip_h, ang = True, math.atan2(-self.vy, -self.vx)
            else:
                ang = math.atan2(self.vy, self.vx)
        else:                        # wave: Wave.cs:~72 spriteDirection = vx < 0 ? 1 : -1, flip when -1
            flip_h, ang = self.vx >= 0, 0.0
        img = _orient(frame, ang, flip_h)
        h, w = img.shape[:2]
        blit(canvas, img, int(round(self.x - w / 2)), int(round(self.y - h / 2)))


# ----------------------------------------------------------------------------------------------
# Calamitas
# ----------------------------------------------------------------------------------------------
class Calamitas:
    def __init__(self, w, h, audio):
        self.w = int(w)
        self.h = int(h)
        self.audio = audio
        # World scale: source distances/speeds are authored for ~1080p; scale so the 550px hover is half the
        # smaller screen side. invented -- presentation only.
        self.k = max(0.25, min(2.0, 0.5 * min(self.w, self.h) / _HOVER_ABOVE))
        self.anims = {n: Anim(n, ANIM_MS[n], loop=(n != _ONE_SHOT)) for n in _ANIM_ORDER}
        self.proj = {n: strip_frames(SPR / ("%s.png" % n), PROJ_FRAMES[n]) for n in PROJ_FRAMES}
        self.shield_top = self._opt_sprite("SupremeShieldTop.png")
        self.shield_bottom = self._opt_sprite("SupremeShieldBottom.png")
        self.x = self.w / 2.0
        self.y = self.h / 2.0
        self.vx = 0.0
        self.vy = 0.0
        self.cx, self.cy = self.x, self.y
        self.bullets = []
        self.current = _IDLE
        self.attack = None
        self.cooldowns = {a["key"]: 0.0 for a in ATTACKS}
        self.next_at = 0.0
        self.t = 0.0
        self.tick_n = 0
        self._acc = 0.0
        self._rng = random.Random()
        # scheduler state (ai[1] / ai[2] / phaseChange analogues)
        self._intro_done = False
        self._phase_change = 0
        self._phase_id = 0
        self._phase_t = 0
        self.stage = 1
        self.will_charge = False   # SupremeCalamitas.cs:~150 `bool willCharge = false`
        self._fire_first = True    # SupremeCalamitas.cs:~151 `bool fireFireblastFirst = true`
        self._can_split = True     # SupremeCalamitas.cs:~150 `bool canFireSplitingFireball = true`
        self._p3_count = 0
        self._pending = None
        # attack runtime
        self._atk_tick = 0
        self._atk_len = 0
        self._shots = []
        self._fired = []
        self._sfx_ticks = set()
        self._dash_starts = []
        self._dash_e0 = None
        self._dash_index = 0
        self._bh_counter = 0
        # shield / facing
        self.shield_op = 1.0       # SupremeCalamitas.cs:~176 `public float shieldOpacity = 1f`
        # forcefield sphere. scale starts at 1f and is the value the original actually animates
        # (SupremeCalamitas.cs:577/612/619); opacity starts at 1f (line ~171) and stays there.
        self.forcefield_scale = 1.0
        self.forcefield_op = 1.0
        self.forcefield_pure = 1.0
        self._ff = _make_forcefield(w, h)
        self.shield_rot = 0.0
        self._shield_active = False
        self._dashing = False
        self._close = False
        self.facing_left = False
        self._tints = {}
        self.seekers = []          # TODO: see _spawn_soul_seekers

    def _opt_sprite(self, name):
        p = SPR / name
        return scaled(p) if p.exists() else None

    # ------------------------------------------------------------------ scheduling
    def pick_attack(self):
        """Return the next ATTACKS dict following the source's phaseChange table (does not start it)."""
        if not self._intro_done:
            self._intro_done = True
            self._phase_id = 0
            self._phase_t = 0
            return _BY_KEY["bullethell"]
        lens = _P2_LEN if self.stage == 2 else _P1_LEN
        if self._phase_id == 0 and self._phase_t < lens[0]:
            return self._slot_attack()
        return self._advance_table()

    def _slot_attack(self):
        suffix = "_p2" if self.stage == 2 else ""
        lens = _P2_LEN if self.stage == 2 else _P1_LEN
        shot_tick = 60 if self.stage == 2 else 90
        if lens[0] - self._phase_t <= shot_tick:
            # tail of the phase is shorter than one shot period: no shot fires, so no state change (invented filler)
            return _BY_KEY["barrage" + suffix]
        # SupremeCalamitas.cs:~2349 int randomShot = Main.rand.Next(6) and the three-way branch below it
        r = self._rng.randrange(6)
        if r == 0 and self._can_split and not self._fire_first:
            kind = "gigablast_hover"
            self._can_split = False
        elif (r == 1 and self._can_split) or self._fire_first:
            kind = "fireblast"
            self._can_split = False
        else:
            kind = "barrage"
            self._can_split = True
        self._fire_first = False
        return _BY_KEY[kind + suffix]

    def _advance_table(self):
        prev = self._phase_id
        self._phase_change = (self._phase_change + 1) % len(_PHASE_TABLE)
        pc = self._phase_change
        if pc in _WILL_CHARGE_AT:
            self.will_charge = _WILL_CHARGE_AT[pc]
        self.stage = 2 if pc >= _STAGE2_FROM else 1
        phase = _PHASE_TABLE[pc]
        if prev == 0:
            self._fire_first = True   # SupremeCalamitas.cs:~2354 `fireFireblastFirst = true` when phase 0 ends
        self._phase_id = phase
        self._phase_t = 0
        suffix = "_p2" if self.stage == 2 else ""
        if phase == 0:
            return self._slot_attack()
        if phase == 1:
            return _BY_KEY["bash" + suffix]
        if phase == 3:
            key = "wave" if self._p3_count % 2 else "hellblast"   # alternation invented (see header)
            self._p3_count += 1
            return _BY_KEY[key + suffix]
        return _BY_KEY["gigablast" + suffix]

    # ------------------------------------------------------------------ main loop
    def update(self, dt, cursor):
        if cursor is not None:
            self.cx, self.cy = float(cursor[0]), float(cursor[1])
        self._acc += max(0.0, float(dt)) * _TPS
        n = 0
        while self._acc >= 1.0 - 1e-6 and n < 8:
            self._acc -= 1.0
            n += 1
            self._tick()
        if self._acc > 2.0:
            self._acc = 0.0
        self.anims[self.current].update(dt)

    def _tick(self):
        self.tick_n += 1
        self.t = self.tick_n / _TPS
        for key, v in self.cooldowns.items():
            if v > 0.0:
                self.cooldowns[key] = max(0.0, v - 1.0 / _TPS)
        self._shield_step()
        self._forcefield_step()
        self._ai()
        self.x += self.vx
        self.y += self.vy
        self._step_bullets()

    def _ai(self):
        if self.attack is None:
            self.current = _IDLE
            if self.t + 1e-9 >= self.next_at:
                if self._pending is None:
                    self._pending = self.pick_attack()
                if self.cooldowns.get(self._pending["key"], 0.0) <= 1e-9:
                    self._begin(self._pending)
                    self._pending = None
            if self.attack is None:
                self._face_cursor()
                return
        self._attack_tick()

    def _face_cursor(self):
        if abs(self.cx - self.x) > _FACE_DEADZONE * self.k:
            self.facing_left = self.cx < self.x

    def _begin(self, a):
        self.attack = a
        self._atk_tick = 0
        ticks = int(round(a["dur"] * _TPS))
        if a["phase"] == 0:
            lens = _P2_LEN if self.stage == 2 else _P1_LEN
            ticks = min(ticks, max(1, lens[0] - self._phase_t))
        self._atk_len = ticks
        self._shots = [(int(round(s[1] * _TPS)), s) for s in a["shots"]]
        self._fired = [False] * len(self._shots)
        if "sfx_at" in a:
            self._sfx_ticks = {int(round(t * _TPS)) for t in a["sfx_at"]}
        else:
            self._sfx_ticks = {tk for tk, _ in self._shots}
        self._dash_starts = [int(round(t * _TPS)) for t in a.get("dash_at", ())]
        self._dash_e0 = None
        self._dash_index = 0
        self._bh_counter = 0
        self.cooldowns[a["key"]] = a["cd"]

    def _finish(self):
        a = self.attack
        self.attack = None
        self.current = _IDLE
        self.next_at = self.t + (a["cd"] if a else _GAP)

    def _attack_tick(self):
        a = self.attack
        e = self._atk_tick
        if e >= self._atk_len:
            self._finish()
            self._face_cursor()
            return
        k = self.k
        self.current = a["anim"]
        for t in a.get("anim_reset_at", ()):
            if int(round(t * _TPS)) == e:
                self.anims[a["anim"]].reset()
        phase = a["phase"]
        if a["key"] == "bullethell":
            self.vx *= _BH_DAMP
            self.vy *= _BH_DAMP
            self._bh1_stream(e)
            self._face_cursor()
        elif phase == 0:
            self._fly(self.cx - self.x, (self.cy - _HOVER_ABOVE * k) - self.y, _HOVER_VEL * k, _HOVER_ACC * k)
            self._face_cursor()
        elif phase in (3, 4):
            # posX = 1; if (NPC.Center.X < player.position.X + player.width) posX = -1;
            side = -1.0 if self.x < self.cx else 1.0
            dist = (_SIDE_P3 if phase == 3 else _SIDE_P4) * k
            self._fly(self.cx + side * dist - self.x, self.cy - self.y, _SIDE_VEL * k, _SIDE_ACC * k)
            self._face_cursor()
        elif phase == 1:
            self._dash_tick(a, e)
        self._fire(a, e)
        if e in self._sfx_ticks:
            self.audio.play(a["sfx"])
        self._atk_tick += 1
        if phase >= 0:
            self._phase_t += 1

    def _fly(self, dx, dy, vmax, acc):
        """CalamityUtils.SmoothMovement(npc, 0f, dist, vel, acc, true) is NOT in the attached files; this is
        vanilla NPC.SimpleFlyMovement toward unit(dist)*vel (the `true` branch). Approximation."""
        n = math.hypot(dx, dy)
        tx = dx / n * vmax if n > 1e-6 else 0.0
        ty = dy / n * vmax if n > 1e-6 else 0.0
        if self.vx < tx:
            self.vx += acc
            if self.vx < 0 and tx > 0:
                self.vx += acc
        elif self.vx > tx:
            self.vx -= acc
            if self.vx > 0 and tx < 0:
                self.vx -= acc
        if self.vy < ty:
            self.vy += acc
            if self.vy < 0 and ty > 0:
                self.vy += acc
        elif self.vy > ty:
            self.vy -= acc
            if self.vy > 0 and ty < 0:
                self.vy -= acc

    def _dash_tick(self, a, e):
        k = self.k
        for i, s in enumerate(self._dash_starts):
            if e == s:
                dx, dy = self.cx - self.x, self.cy - self.y
                n = math.hypot(dx, dy)
                if n < 1e-6:
                    dx, dy, n = 0.0, 1.0, 1.0
                v = a["dash"] * k
                self.vx, self.vy = dx / n * v, dy / n * v
                self.shield_rot = math.atan2(self.vy, self.vx)   # shieldRotation = velocity.ToRotation()
                self._dash_e0 = s
                self._dash_index = i
                return
        if self._dash_e0 is None:
            return
        t2 = e - self._dash_e0   # ai[2]
        if t2 >= _DASH_DAMP_FROM:
            self.vx *= _DASH_DAMP
            self.vy *= _DASH_DAMP
            if -_DASH_STOP * k < self.vx < _DASH_STOP * k:
                self.vx = 0.0
            if -_DASH_STOP * k < self.vy < _DASH_STOP * k:
                self.vy = 0.0
        if abs(self.vx) > _FACE_DASH_MIN * k:
            self.facing_left = self.vx < 0
        # willChargeAgain && ai[2] > 50: re-aim the shield for the next dash (~2560)
        if self._dash_index + 1 < len(self._dash_starts) and t2 > 50:
            ideal = math.atan2(self.cy - self.y, self.cx - self.x)
            self.shield_rot = _angle_lerp(self.shield_rot, ideal, _SHIELD_LERP)
            self.shield_rot = _angle_towards(self.shield_rot, ideal, _SHIELD_TOWARDS)

    def _fire(self, a, e):
        for i, (tk, shot) in enumerate(self._shots):
            if self._fired[i] or tk > e:
                continue
            self._fired[i] = True
            name, _off, speed, homing, spin = shot
            muzzle = a.get("muzzle", 0.0)
            fan = a.get("fan")
            cap = speed * a["cap_mult"] if "cap_mult" in a else None
            if fan:
                cnt, arc = fan
                for j in range(cnt):
                    ang = -arc + (2 * arc) * j / (cnt - 1)
                    self.spawn(name, speed, homing, spin, angle=ang, muzzle=muzzle, cap=cap)
            else:
                self.spawn(name, speed, homing, spin, muzzle=muzzle, cap=cap)

    def spawn(self, name, speed, homing=0.0, spin=0.0, angle=0.0, muzzle=0.0, cap=None,
              origin=None, direction=None, ai1=2.0):
        """Spawn a projectile aimed at the cursor. speed: source px per projectile update; homing: >0 enables
        the projectile's own steering; spin: radians/update added to the heading (0 in the source)."""
        ox, oy = origin if origin is not None else (self.x, self.y)
        if direction is None:
            dx, dy = self.cx - ox, self.cy - oy
        else:
            dx, dy = direction
        n = math.hypot(dx, dy)
        if n < 1e-6:
            dx, dy, n = 0.0, 1.0, 1.0   # SafeNormalize(Vector2.UnitY)
        dx, dy = dx / n, dy / n
        if angle:
            c, s = math.cos(angle), math.sin(angle)
            dx, dy = dx * c - dy * s, dx * s + dy * c
        k = self.k
        p = Projectile(name, self.proj[name], ox + dx * muzzle * k, oy + dy * muzzle * k,
                       dx * speed * k, dy * speed * k, k=k, homing=homing, spin=spin,
                       cap=(cap * k if cap is not None else None), ai1=ai1)
        self.bullets.append(p)
        return p

    def _raw_shot(self, name, x, y, vx, vy):
        k = self.k
        self.bullets.append(Projectile(name, self.proj[name], x, y, vx * k, vy * k, k=k, ai1=2.0))

    def _bh1_stream(self, e):
        """BH1 projectile stream, SupremeCalamitas.cs:~1075-1115 (#region FirstAttack), non-zenith, revenge gate."""
        c2 = e + 1                       # bulletHellCounter2 is incremented before use
        self._bh_counter += 1
        if self._bh_counter < _BH_GATE:
            return
        self._bh_counter = 0
        k, rnd, px, py, U = self.k, self._rng, self.cx, self.cy, _UDIELUL
        n = "BrimstoneHellblast2"
        if c2 % (_BH_GATE * 6) == 0:     # horizontal blast
            dist = rnd.choice((-1000.0, 1000.0))
            self._raw_shot(n, px + dist * k, py, (4.0 if dist == -1000.0 else -4.0) * U, 0.0)
        if c2 < 300:                     # from above
            self._raw_shot(n, px + rnd.randint(-1000, 1000) * k, py - 1000 * k, 0.0, 4.0 * U)
        elif c2 < 600:                   # from left and right
            self._raw_shot(n, px + 1000 * k, py + rnd.randint(-1000, 1000) * k, -3.5 * U, 0.0)
            self._raw_shot(n, px - 1000 * k, py + rnd.randint(-1000, 1000) * k, 3.5 * U, 0.0)
        else:                            # above, left and right
            self._raw_shot(n, px + rnd.randint(-1000, 1000) * k, py - 1000 * k, 0.0, 3.0 * U)
            self._raw_shot(n, px + 1000 * k, py + rnd.randint(-1000, 1000) * k, -3.0 * U, 0.0)
            self._raw_shot(n, px - 1000 * k, py + rnd.randint(-1000, 1000) * k, 3.0 * U, 0.0)

    def _on_projectile_death(self, p):
        sfx = _IMPACT_SFX.get(p.name)
        if sfx:
            self.audio.play(sfx)
        ring = _RING.get(p.name)
        if ring:
            cnt, speed = ring
            for i in range(cnt):
                ang = 2 * math.pi * i / cnt
                # Vector2(0, -speed).RotatedBy(ang)
                self.spawn("BrimstoneBarrage", speed, 0.0, 0.0, origin=(p.x, p.y),
                           direction=(math.sin(ang), -math.cos(ang)), cap=speed * _RING_CAP_MULT, ai1=p.ai1)

    def _step_bullets(self):
        cur = (self.cx, self.cy)
        for p in list(self.bullets):
            p.step(cur, self)
        m = 1100.0 * self.k + 200.0
        self.bullets[:] = [p for p in self.bullets
                           if p.alive and -m < p.x < self.w + m and -m < p.y < self.h + m]
        if len(self.bullets) > 800:   # invented -- perf safety
            del self.bullets[:len(self.bullets) - 800]

    # ------------------------------------------------------------------ shield + forcefield
    def _forcefield_step(self):
        """SupremeCalamitas.cs:573-620.

        The original gates the sphere on forcefieldScale, not opacity: it lerps toward 0.45 while
        the shield is up (:612, "shrink the force-field since it looks strange when charging") and
        back toward 1 otherwise (:619). So `active` here mirrors the shield's active test, and the
        sphere shrinks in step with the shield rather than appearing and disappearing.

        forcefieldOpacity is left at its 1f default (:171). The original only lowers it at BH4
        (:1304 -> 0.4) and post-music-hit (:1325 -> 0.7), both outside the pet's scope, so
        driving it would invent a state the fight never reaches.
        """
        target = _FF_SHRINK if self._shield_active else 1.0
        self.forcefield_scale += (target - self.forcefield_scale) * _FF_LERP
        ff = self._ff
        if ff is not None:
            # `scale` must be set BEFORE update(): Forcefield.quad is derived in _refresh(), which
            # update() calls. Assigning it afterwards leaves the previous frame's quad in place.
            h = self.anims[self.current].frame(self.facing_left).shape[0]
            ff.scale = (_FF_VS_SPRITE * h) / 216.0
            ff.update(1.0 / FPS, {
                "forcefieldScale": self.forcefield_scale,
                "forcefieldOpacity": self.forcefield_op,
                "forcefieldPureVisualScale": self.forcefield_pure,
                # `willCharge` only recolours the sphere when it is not already dashing
                # (SupremeCalamitas.cs:3720, gated on `willCharge && ai[1] != 2f`)
                "willCharge": bool(self.will_charge and not self._dashing),
            })

    def _shield_step(self):
        a = self.attack
        phase = a["phase"] if a else None
        lens = _P2_LEN if self.stage == 2 else _P1_LEN
        dashing = phase == 1
        close = bool(a) and phase in lens and self._phase_t >= lens[phase] - _CLOSE_MARGIN
        in_bh = bool(a) and a["key"] == "bullethell"   # shouldNotUseShield: bulletHellCounter2 % 900 != 0
        self._dashing, self._close = dashing, close
        active = ((self.will_charge and close) or dashing) and not in_bh
        self._shield_active = active
        if active:
            if not dashing:
                ideal = math.atan2(self.cy - self.y, self.cx - self.x)
                if abs(_wrap(self.shield_rot - ideal)) > _SHIELD_MIN_OFF:
                    self.shield_rot = _angle_lerp(self.shield_rot, ideal, _SHIELD_LERP)
                    self.shield_rot = _angle_towards(self.shield_rot, ideal, _SHIELD_TOWARDS)
            target = 1.0
        else:
            target = 0.0
        self.shield_op += (target - self.shield_op) * _SHIELD_FADE

    # ------------------------------------------------------------------ drawing
    def _tint(self, img):
        hit = self._tints.get(id(img))
        if hit is not None:
            return hit[1]
        out = img.copy()
        rgb = out[:, :, :3].astype(np.float32)
        rgb = rgb * (1.0 - _WILL_CHARGE_TINT) + np.array([255.0, 0.0, 0.0], np.float32) * _WILL_CHARGE_TINT
        out[:, :, :3] = rgb.astype(np.uint8)
        self._tints[id(img)] = (img, out)
        return out

    def draw(self, canvas):
        # DrawForcefield runs before the body in the original PreDraw (~3663, ahead of the body
        # draw), so the sphere is composited first and she sits inside it.
        self._draw_forcefield(canvas)
        img = self.anims[self.current].frame(self.facing_left)
        if self.will_charge:
            img = self._tint(img)
        h, w = img.shape[:2]
        # crop-box origin offsets were not shipped with the frames -> centred anchor (invented, presentation only)
        blit(canvas, img, int(round(self.x - w / 2)), int(round(self.y - h / 2)))
        if self.shield_op > _SHIELD_SOLID and self.shield_top is not None and self.shield_bottom is not None:
            self._draw_shield(canvas)
        for b in self.bullets:
            b.draw(canvas)

    def _draw_forcefield(self, canvas):
        """SupremeCalamitas.cs:3669 DrawForcefield.

        Size is set in _forcefield_step(), before update(), because that is when Forcefield.quad
        is derived. Two mappings are invented, because the shipped frames give us neither quantity:

        - Centre. The original draws the sphere on NPC.Center. This pet already anchors her sprite on
          her centre (`y - h/2` in draw()), so self.x/self.y are the right point and need no offset.
        - Size. The C# quad is 216px against a hitbox of 216/1.4142 = 152.8px, i.e. 1.414x the
          hitbox. There is no hitbox in the pet, so the sphere is sized against her sprite height
          instead (_FF_VS_SPRITE) and follows SCALE with it.
        """
        ff = self._ff
        if ff is None or canvas.shape[2] != 4:
            return
        ff.draw(canvas, (self.x, self.y))

    def _draw_shield(self, canvas):
        """SupremeCalamitas.cs:~3775 DrawShield."""
        rot = self.shield_rot
        jaw_off = 0.0
        if self._dashing:
            jaw_off = _JAW_DASH
        elif self.will_charge and self._close:
            lo, hi, rate = _JAW_LAUGH
            jaw_off = lo + (hi - lo) * (math.sin(self.t * rate) * 0.5 + 0.5)
        flip_v = math.cos(rot) <= 0
        px = self.x + math.cos(rot) * _SHIELD_FWD * SCALE
        py = self.y + math.sin(rot) * _SHIELD_FWD * SCALE
        if flip_v:
            a = rot - math.pi / 2
        else:
            a = rot + math.pi / 2
            jaw_off = -jaw_off
        jx = px + math.cos(a) * _JAW_OFF * SCALE
        jy = py + math.sin(a) * _JAW_OFF * SCALE
        jaw = _orient(self.shield_bottom, rot + jaw_off, False, flip_v)
        skull = _orient(self.shield_top, rot, False, flip_v)
        blit(canvas, jaw, int(round(jx - jaw.shape[1] / 2)), int(round(jy - jaw.shape[0] / 2)))
        blit(canvas, skull, int(round(px - skull.shape[1] / 2)), int(round(py - skull.shape[0] / 2)))

    # ------------------------------------------------------------------ stubs
    def _spawn_soul_seekers(self):
        """TODO (not called): Soul Seekers. Source: SupremeCalamitas.cs:~2075 `if (IsAtSeekers)` (lifeRatio <= 0.2,
        i.e. AFTER the brothers, not before as the brief says): 10 SoulSeekerSupreme (20 in getGoodWorld) in a ring at
        225 px (300), SoulSeekerSupreme.cs:~190 RotationalDegreeOffset += 0.5 per tick; every 180 ticks (~118) each
        fires one BrimstoneBarrage (ai[1]=3, speed 5*0.5, cap 15) if 160 < dist < 1952; 28000 HP, DR 0.25.
        Needs a sprite: SoulSeekerSupreme.png (6 frames, ~40x40) + SoulSeekerSupremeGlow.png. Nothing is spawned."""
        self.seekers = []


# ----------------------------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------------------------
def main():
    try:
        win = LayeredWindow()
    except Exception as exc:
        print("could not create layered window:", exc)
        return 1
    audio = Audio()
    try:
        cal = Calamitas(win.w, win.h, audio)
    except Exception as exc:
        print("could not load assets:", exc)
        win.close()
        audio.close()
        return 1
    try:
        ctypes.WinDLL("winmm").timeBeginPeriod(1)
    except Exception:
        pass
    period = 1.0 / FPS
    last = time.perf_counter()
    try:
        while win.pump():
            if win.esc_pressed():
                break
            start = time.perf_counter()
            dt = min(start - last, 0.1)
            last = start
            cal.update(dt, win.cursor())
            win.clear()
            cal.draw(win.buf)
            win.present()
            spare = start + period - time.perf_counter()
            if spare > 0:
                time.sleep(spare)
    finally:
        print(win.diagnostics())
        win.close()
        audio.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())