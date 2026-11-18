"""Fresh-process App initialization benchmark, for this patch or its base.

python tools/benchmark_startup.py --root . --models 4 --runs 3
python tools/benchmark_startup.py --root ../original-checkout --models 4 --runs 3

Use xvfb-run on headless Linux. Excludes Python imports, splash, tray, native
hooks, audio, and network; includes indexing and all model work done by App.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def sample(root, count):
    sys.path.insert(0, str(root)); os.chdir(root)
    # Import-only compatibility shims for the original Windows-oriented code.
    # No native keyboard hooks or system tray are installed by this benchmark.
    if not hasattr(ctypes, "WINFUNCTYPE"):
        ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    from PySide6 import QtGui, QtWidgets
    if not hasattr(QtWidgets, "QShortcut"):
        QtWidgets.QShortcut = QtGui.QShortcut
    from bapet.app import App
    from bapet.gltf import GLTF

    paths = sorted((root / "model").glob("*.glb"))[:count]
    if not paths: raise SystemExit("No models found in the selected checkout")
    qapp = QtWidgets.QApplication([])
    reads = []
    original = GLTF.__init__
    def track(reader, path):
        reads.append(str(path)); original(reader, path)
    with tempfile.TemporaryDirectory() as directory, ExitStack() as patches:
        for module in ("bapet.app", "bapet.app_settings"):
            patches.enter_context(patch(module + ".SETTINGS_PATH", Path(directory) / "settings.json"))
        patches.enter_context(patch.object(GLTF, "__init__", track))
        patches.enter_context(patch.object(App, "setup_tray", lambda a: setattr(a, "tray", None)))
        patches.enter_context(patch.object(App, "setup_hotkey", lambda a: None))
        patches.enter_context(patch.object(App, "setup_rag_sound", lambda a: None))
        args = SimpleNamespace(scale=None, fps=None, dump=False, no_mask=True)
        start = time.perf_counter()
        app = App(qapp, paths, args, start_hidden=True)
        elapsed = time.perf_counter() - start
        peak = None
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak = round(peak / (1048576 if sys.platform == "darwin" else 1024), 1)
        except ImportError:
            pass
        print("SAMPLE " + json.dumps(dict(models=len(paths), seconds=round(elapsed, 4),
              peak_rss_mib=peak, glb_reads=len(reads), pet_windows=len(app.pets))), flush=True)
        app.timer.stop(); app.watcher.stop()
        if hasattr(app, "models"): app.models.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--models", type=int, default=4)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--sample", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if min(args.models, args.runs) < 1: ap.error("models and runs must be positive")
    if args.sample:
        sample(args.root.resolve(), args.models)
    else:
        rows = []
        for _ in range(args.runs):
            command = [sys.executable, str(Path(__file__).resolve()), "--sample", "--root",
                       str(args.root.resolve()), "--models", str(args.models)]
            output = subprocess.check_output(command, text=True, env=dict(os.environ, OPENBLAS_NUM_THREADS="1"))
            row = json.loads(next(line[7:] for line in output.splitlines() if line.startswith("SAMPLE ")))
            print(row, flush=True); rows.append(row)
        print("Median initialization:", statistics.median(r["seconds"] for r in rows), "seconds")
        if rows[0]["peak_rss_mib"] is not None:
            print("Median process peak RSS:", statistics.median(r["peak_rss_mib"] for r in rows), "MiB")