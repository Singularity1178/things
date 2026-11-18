"""Demand boundaries: catalog -> selected character -> deployed desktop window."""
from __future__ import annotations

import traceback

from PySide6 import QtCore, QtWidgets
from voicelines import sync_deployed

from .character import Character
from .pet import Pet
from .renderer import Renderer


class AppModels:
    @property
    def ctx(self):
        if self._ctx is None:
            import moderngl
            ctx = moderngl.create_standalone_context(require=330)
            ctx.gc_mode = "context_gc"
            self._ctx = ctx
            print("GL:", ctx.info.get("GL_RENDERER"))
        return self._ctx

    def decode_student(self, slot):
        ch = Character(slot.path, slot.index, False, slot.profile)
        ch.name = slot.name
        # QImage decoding and alpha bleed are CPU work. No QWidget or GL call
        # is allowed here: this function runs on the single loader thread.
        for src in {p.src for p in ch.model.prims}:
            ch.model.get_image(src)
        return ch

    def upload_student(self, ch):
        ch.renderer = Renderer(ch.model, ch.fit, self.ctx)
        ch.model.img_cache.clear()

    def prepare_models(self, slots):
        """Pin an explicit selection until its consumer takes ownership."""
        slots = list(dict.fromkeys(slots))
        ready = {s: ch for s in slots if (ch := self.models.peek(s)) is not None}
        todo = [s for s in slots if s not in ready]
        if not todo:
            return ready
        if self.loading_models:
            return None
        self.loading_models = True
        dialog = QtWidgets.QProgressDialog("Preparing models...", "Cancel", 0, len(slots))
        dialog.setWindowTitle("Loading selected students")
        dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        timer = QtCore.QTimer(dialog)
        error = []

        def advance():
            if not todo or error: return
            s = todo[0]
            dialog.setLabelText(f"Loading {s.name} ({len(ready) + 1}/{len(slots)})")
            try:
                ch = self.models.poll(s)
            except Exception as exc:
                error.append(str(exc)); timer.stop(); dialog.reject(); return
            if ch is None:
                return
            ready[s] = ch
            todo.pop(0)
            dialog.setValue(len(ready))
            if not todo:
                timer.stop(); dialog.accept()

        try:
            timer.timeout.connect(advance)
            dialog.canceled.connect(dialog.reject)
            timer.start(16)
            dialog.exec()
        finally:
            timer.stop(); dialog.deleteLater()
            if todo: self.models.abandon()
            self.loading_models = False
        if error:
            self.models.errors.pop(todo[0], None)  # explicit actions may retry a repaired file
            QtWidgets.QMessageBox.warning(None, "Could not load student", error[0])
        return ready if not todo else None

    def ensure_pet(self, slot, models=None):
        if slot.pet is not None:
            return slot.pet
        models = self.prepare_models([slot]) if models is None else models
        if models is None:
            return None
        pet = Pet(self, models[slot])
        pet.slot = slot
        pet.spawn(slot.index, show=False)
        self.pets.append(pet)
        self.z_base.append(pet)
        slot.pet = pet
        sync_deployed(slot.path.stem)
        if self.active is None:
            self.set_active(pet)
        return pet

    def recall_pet(self, pet):
        self.cfg["positions"][pet.name] = pet.feet_pos()[0]
        pet.conv.abort()
        pet.rag_clear(); pet.hide()
        pet.set_ride(False, fade=False)
        for other in self.pets:
            if other is pet:
                continue
            # Release transient references before deleting a Qt window.
            if other.gaze_peer is pet:
                other.gaze_peer = None
            if other.meet_target is pet or other.chat_with is pet:
                other.conv.abort(); other.meet_target = None
            atk, flee = other.atk or {}, other.flee or {}
            if (atk.get("target") is pet or pet in atk.get("others", ())
                    or (atk.get("cover") or {}).get("shield") is pet or flee.get("src") is pet):
                other.combat_over()
        for group in (self.pets, self.z_base, self.z_order):
            if pet in group:
                group.remove(pet)
        pet.slot.pet = None
        if self.active is pet:
            self.set_active(next(iter(self.pets), None))
        pet.conv.pet = None  # Break the pet <-> Conversation ownership cycle at recall.
        pet.deleteLater()

    def show_team(self, slots):
        ready = self.prepare_models(slots)
        if ready is None:
            return False
        for slot in slots:
            try:
                p = self.ensure_pet(slot, ready)
                p.show()
            except Exception:
                traceback.print_exc()
        self.panel.sync_char_rows()
        return True