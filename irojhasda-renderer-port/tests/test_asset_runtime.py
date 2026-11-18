"""Opt-in integration test: Python 3.13, Qt, real models and OpenGL 3.3.

Windows: python test_asset_runtime.py --live
Linux:   xvfb-run -a python test_asset_runtime.py --live
No personal settings are read/written and no voice requests are made.
"""
from __future__ import annotations

import gc
import os
import shutil
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def run():
    from PySide6 import QtCore, QtWidgets
    from bapet.app import App
    from bapet.gltf import GLTF
    from babattlearena import install
    from babattlearena.campaign_data import Run

    q = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    q.setQuitOnLastWindowClosed(False)
    original = sorted((Path(__file__).resolve().parent / "model").glob("*.glb"))
    assert original, "The test needs at least one real model in model/."
    def pump(seconds=0.1):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            q.processEvents(); time.sleep(0.002)
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
        tmp = Path(folder)
        for mod, key, filename in [("bapet.app", "SETTINGS_PATH", "pets.json"),
                                   ("bapet.app_settings", "SETTINGS_PATH", "pets.json"),
                                   ("babattlearena.settings", "SETTINGS_PATH", "arena.json"),
                                   ("babattlearena.campaign_data", "SAVE_PATH", "campaign.json")]:
            stack.enter_context(patch(f"{mod}.{key}", tmp / filename))
        stack.enter_context(patch("bapet.app_models.sync_deployed"))
        stack.enter_context(patch("bapet.app_hotkey.AppHotkey.setup_hotkey"))
        paths = list(original[:200])
        while len(paths) < 200:
            path = tmp / f"Extra {len(paths):03}.glb"
            try: os.link(original[0], path)
            except OSError: shutil.copyfile(original[0], path)
            paths.append(path)
        reads = []
        qt_errors = []
        original_hook = sys.excepthook
        def capture_exception(kind, value, tb):
            qt_errors.append(str(value)); original_hook(kind, value, tb)
        stack.enter_context(patch.object(sys, "excepthook", capture_exception))
        real_init = GLTF.__init__
        def tracked(reader, path):
            reads.append(Path(path).name); real_init(reader, path)
        stack.enter_context(patch.object(GLTF, "__init__", tracked))
        args = SimpleNamespace(dump=False, scale=None, fps=30, no_mask=True)
        app = App(q, paths, args, start_hidden=True)
        mgr = install(app)
        try:
            app.start_with_title(); pump()
            mgr.open_lobby(); pump()
            pk = app.picker(); entries = pk.entries
            pk.portraits.folder = tmp / "portraits"; pk.portraits.keys.clear()
            for entry in entries: assert not entry.still.isNull()
            mgr.open_campaign(); pump()
            assert len(app.roster) == 200 and not app.pets and not reads
            assert app._ctx is None
            assert len(pk.portraits.recent) <= 48
            print("PASS: 200 entries; title, both lobbies and all portraits: zero GLB reads/windows")

            mgr._hide(mgr._campaign_screen()); mgr.open_desktop_pets(); pk.clear(); pump()
            extra = entries[-1]
            pk.toggle(extra)
            deadline = time.monotonic() + 15
            while extra.img is None and time.monotonic() < deadline: pump(0.03)
            assert extra.img is not None and reads == [extra.file] and not app.pets
            assert pk.portraits.has(extra.slot)
            pk.deploy(); pump(0.7)
            assert len(app.pets) == 1 and app.pets[0].isVisible() and len(reads) == 1
            assert app.cfg["deployed"] == [extra.name]
            pk.hide(); pump()
            assert extra.preview is None and extra.clip is None
            print("PASS: live custom preview, portrait cache, and deployment share one model")

            teams = [[entries[0]], [entries[1]]]
            mgr.begin_battle(teams); pump(0.25)
            assert mgr.battle is not None and len(mgr.battle.fighters) == 2
            assert not app.pets and app.cfg["deployed"] == [extra.name]
            assert set(reads) == {extra.file, entries[0].file, entries[1].file}
            assert all(f.ch.renderer is not None for f in mgr.battle.fighters)
            mgr.to_title(); pump()
            assert len(app.pets) == 1 and app.pets[0].name == extra.name
            print("PASS: two-fighter arena loads only its participants and restores the desktop")

            camp = mgr.campaign_ctl()
            camp.run = Run(camp.save, [entries[0].name], entries[0].name,
                           [entries[1].name, entries[2].name, entries[3].name], seed=4)
            door = next(i for i, d in enumerate(camp.run.doors) if d["kind"] not in ("cafe", "stall"))
            before = len(reads); camp.enter_door(door); pump(0.25)
            assert mgr.battle is not None and mgr.battle.campaign is not None
            names = {f.ch.path.name for f in mgr.battle.fighters}
            assert set(reads[before:]) <= names and not app.pets
            mgr.to_title(); pump()
            app.set_pet_visible(app.pets[0], False); pump()
            gc.collect(); app.ctx.gc()
            assert not app.pets and len(app.models.live) <= 2
            assert app.cfg["deployed"] == []
            assert not qt_errors, qt_errors
            print("PASS: campaign room demand, recall, and two-model idle retention")
        finally:
            app.timer.stop(); app.watcher.stop(); app.models.close()
            app.save_timer.stop()
            if mgr.battle is not None: mgr.end_battle()
            for w in q.topLevelWidgets(): w.hide()
            q.processEvents(); app.ctx.gc()


if __name__ == "__main__":
    if "--live" in sys.argv:
        run()
    else:
        print(__doc__)