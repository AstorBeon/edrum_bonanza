from __future__ import annotations
import copy
import math
from pathlib import Path
import queue
import tempfile
import threading
import time
import uuid

import cv2
import mido
import numpy as np
import pygame
from PySide6 import QtCore, QtGui, QtWidgets as W
from .core import Settings, Timeline, read_sample, mux_video
from .devices import CameraStream


class Window(W.QMainWindow):
    export_done = QtCore.Signal(str)
    devices_done = QtCore.Signal(object, object, str)

    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.timeline = Timeline()
        self.events = queue.Queue()
        self.port = None
        self.camera = None
        self.writer = None
        self.video_path = None
        self.last_frame = None
        self.video_frames = 0
        self.sounds = {}
        self.channels = {}
        self.learn_row = None
        self.busy = False
        self.dirty_take = False
        self.temp = tempfile.TemporaryDirectory(prefix="edrum-")
        self.setWindowTitle("E-Drum Bonanza")
        self.resize(1200, 850)
        self.audio_error = ""
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=256)
            pygame.mixer.set_num_channels(64)
        except pygame.error as exc:
            self.audio_error = str(exc)
        self.tabs = W.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.build_test()
        self.build_record()
        self.build_options()
        about = W.QLabel(
            "E-Drum Bonanza\n\nLocal MIDI drum studio • Python / Qt\n\nTest your kit, build sound sets, and record synchronized takes.\nSettings and imported samples stay on this computer.\n\nKeyboard: 1–9 and 0 trigger pads on Test and Record.\nMP4 export uses FFmpeg; camera and MIDI hardware are optional.\n\nMIDI notes/channels differ between kit manufacturers. Use Learn to map yours.\nHi-hat CC4 selects the open/closed pad; polyphonic aftertouch chokes a note.\nActual device latency and Windows packaging require testing on the target PC."
        )
        about.setMargin(32)
        self.tabs.addTab(about, "About")
        self.export_done.connect(self.on_export_done)
        self.devices_done.connect(self.on_devices)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(15)
        self.restore_preferences()
        self.refresh_settings()
        self.refresh_devices()
        self.statusBar().showMessage(
            "Audio unavailable: " + self.audio_error
            if self.audio_error
            else "Ready • connect a kit or use the pads"
        )
        self.hihat = 127
        for index, key in enumerate("1234567890"):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(
                lambda i=index: (
                    self.simulate(i) if self.tabs.currentIndex() < 2 else None
                )
            )

    def button(self, text, callback, layout):
        item = W.QPushButton(text)
        item.clicked.connect(callback)
        layout.addWidget(item)
        return item

    def combo(self, layout):
        item = W.QComboBox()
        layout.addWidget(item)
        return item

    def build_test(self):
        page = W.QWidget()
        layout = W.QVBoxLayout(page)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(W.QLabel("MIDI input"))
        self.midi_box = self.combo(row)
        self.midi_box.currentIndexChanged.connect(self.connect_midi)
        self.refresh_button = self.button("Refresh devices", self.refresh_devices, row)
        row.addWidget(W.QLabel("Kit preset"))
        self.preset_box = self.combo(row)
        self.preset_box.currentTextChanged.connect(self.select_preset)
        row.addWidget(W.QLabel("Sound set"))
        self.sound_box = self.combo(row)
        self.sound_box.currentTextChanged.connect(self.select_sound)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(W.QLabel("Master volume"))
        self.volume = W.QSlider(QtCore.Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(self.settings.data.get("volume", 80))
        row.addWidget(self.volume)
        self.volume.valueChanged.connect(self.save_volume)
        self.velocity = W.QSpinBox()
        self.velocity.setRange(1, 127)
        self.velocity.setValue(100)
        row.addWidget(W.QLabel("Test velocity"))
        row.addWidget(self.velocity)
        pad_area = W.QScrollArea()
        pad_area.setWidgetResizable(True)
        pad_area.setMinimumHeight(185)
        pad_widget = W.QWidget()
        self.pad_layout = W.QGridLayout(pad_widget)
        pad_area.setWidget(pad_widget)
        layout.addWidget(pad_area)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(W.QLabel("Camera"))
        self.camera_box = self.combo(row)
        self.preview_check = W.QCheckBox("Camera preview")
        row.addWidget(self.preview_check)
        self.preview_check.toggled.connect(self.change_camera)
        self.camera_box.currentIndexChanged.connect(self.change_camera)
        self.preview = W.QLabel("Camera off")
        self.preview.setMinimumHeight(180)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.preview)
        self.log = W.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        layout.addWidget(self.log)
        self.tabs.addTab(page, "Test")

    def build_record(self):
        page = W.QWidget()
        layout = W.QVBoxLayout(page)
        layout.addWidget(
            W.QLabel(
                "Uses the MIDI input, preset, sound set and camera selected on Test.\nExports contain rendered drum samples; microphone audio is not recorded."
            )
        )
        row = W.QHBoxLayout()
        layout.addLayout(row)
        self.record_button = self.button("● Record", self.start_record, row)
        self.pause_button = self.button("Pause / Resume", self.pause_record, row)
        self.stop_button = self.button("Stop", self.stop_record, row)
        self.record_video = W.QCheckBox("Include camera video")
        row.addWidget(self.record_video)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        self.bpm = W.QSpinBox()
        self.bpm.setRange(30, 300)
        self.bpm.setValue(120)
        self.beats = W.QSpinBox()
        self.beats.setRange(1, 12)
        self.beats.setValue(4)
        row.addWidget(W.QLabel("Tempo"))
        row.addWidget(self.bpm)
        row.addWidget(W.QLabel("Beats per bar (quarter notes)"))
        row.addWidget(self.beats)
        self.metronome = W.QCheckBox("Metronome")
        row.addWidget(self.metronome)
        self.count_in = W.QCheckBox("One-bar count-in")
        row.addWidget(self.count_in)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        self.backing_path = ""
        self.backing_button = self.button("Load backing WAV", self.load_backing, row)
        self.backing_label = W.QLabel("No backing track")
        row.addWidget(self.backing_label)
        self.backing_gain = W.QSlider(QtCore.Qt.Horizontal)
        self.backing_gain.setRange(0, 100)
        self.backing_gain.setValue(50)
        row.addWidget(self.backing_gain)
        self.include_backing = W.QCheckBox("Include backing in exports")
        row.addWidget(self.include_backing)
        self.button("Clear backing", self.clear_backing, row)
        self.elapsed_label = W.QLabel("Ready to record")
        self.elapsed_label.setStyleSheet("font-size:28px;padding:24px")
        layout.addWidget(self.elapsed_label)
        self.record_preview = W.QLabel("Video disabled")
        self.record_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.record_preview.setMinimumHeight(240)
        layout.addWidget(self.record_preview)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        self.export_buttons = [
            self.button(
                "Save " + kind, lambda checked=False, k=kind: self.export(k), row
            )
            for kind in ("WAV", "MP4", "CSV", "JSON", "MIDI")
        ]
        self.button("Output folder", self.choose_folder, layout)
        layout.addStretch()
        self.tabs.addTab(page, "Record")

    def build_options(self):
        page = W.QWidget()
        layout = W.QVBoxLayout(page)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(W.QLabel("Kit preset"))
        self.options_preset_box = self.combo(row)
        self.options_preset_box.currentTextChanged.connect(self.select_preset)
        row.addWidget(W.QLabel("Sound set to edit"))
        self.options_sound_box = self.combo(row)
        self.options_sound_box.currentTextChanged.connect(self.select_sound)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        self.button("New preset", self.new_preset, row)
        self.button("Duplicate preset", self.duplicate_preset, row)
        self.button("Delete preset", self.delete_preset, row)
        self.button("Add input", self.add_pad, row)
        self.button("Remove input", self.remove_pad, row)
        self.button("Learn selected input", self.learn, row)
        self.button("Save mappings", self.save_table, row)
        self.table = W.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Name",
                "Note (0–127)",
                "Channel (0 = any)",
                "Device (* = any)",
                "Preset gain (0–4)",
                "Sensitivity (0.1–4)",
                "WAV sample",
                "Sound volume",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(W.QHeaderView.Interactive)
        for column, width in enumerate((150, 110, 150, 160, 120, 130, 240, 135)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        self.button("New sound set", self.new_sound_set, row)
        self.button("Delete sound set", self.delete_sound_set, row)
        self.button("Assign WAV to selected input", self.assign_sample, row)
        self.button("Preview sample", self.preview_sample, row)
        self.button("Velocity layers", self.edit_layers, row)
        row = W.QHBoxLayout()
        layout.addLayout(row)
        self.button("Export presets + samples", self.export_bundle, row)
        self.button("Import presets + samples", self.import_bundle, row)
        self.last_hit = W.QLabel("Last MIDI input: none")
        layout.addWidget(self.last_hit)
        layout.addWidget(
            W.QLabel(
                "Select the preset and sound set above. New presets start empty; new sound sets have no WAV assignments.\nImported WAV files are copied into application storage.\nKeyboard pads work without MIDI hardware. Save mapping edits before switching presets."
            )
        )
        self.tabs.addTab(page, "Options")

    def error(self, message):
        W.QMessageBox.warning(self, "E-Drum Bonanza", str(message))

    def restore_preferences(self):
        blockers = [
            QtCore.QSignalBlocker(widget)
            for widget in (
                self.bpm,
                self.beats,
                self.metronome,
                self.count_in,
                self.record_video,
                self.preview_check,
                self.backing_gain,
                self.include_backing,
            )
        ]
        saved = self.settings.data
        self.bpm.setValue(saved.get("bpm", 120))
        self.beats.setValue(saved.get("beats", 4))
        self.metronome.setChecked(saved.get("metronome", False))
        self.count_in.setChecked(saved.get("count_in", False))
        self.record_video.setChecked(saved.get("video", False))
        self.preview_check.setChecked(saved.get("preview", False))
        self.backing_path = saved.get("backing_path", "")
        if self.backing_path and not Path(self.backing_path).is_file():
            self.backing_path = ""
        self.backing_label.setText(
            saved.get("backing_name", "Backing WAV")
            if self.backing_path
            else "No backing track"
        )
        self.backing_gain.setValue(saved.get("backing_gain", 50))
        self.include_backing.setChecked(saved.get("include_backing", False))
        if getattr(self, "preferences_connected", False):
            return
        self.preferences_connected = True
        for widget in (self.bpm, self.beats, self.backing_gain):
            widget.valueChanged.connect(self.save_preferences)
        for widget in (
            self.metronome,
            self.count_in,
            self.record_video,
            self.preview_check,
            self.include_backing,
        ):
            widget.toggled.connect(self.save_preferences)

    def save_preferences(self):
        self.settings.data.update(
            bpm=self.bpm.value(),
            beats=self.beats.value(),
            metronome=self.metronome.isChecked(),
            count_in=self.count_in.isChecked(),
            video=self.record_video.isChecked(),
            preview=self.preview_check.isChecked(),
            backing_path=self.backing_path,
            backing_name=self.backing_label.text(),
            backing_gain=self.backing_gain.value(),
            include_backing=self.include_backing.isChecked(),
        )
        self.settings.save()

    def save_volume(self, value):
        self.settings.data["volume"] = value
        self.settings.save()

    def refresh_settings(self):
        self.learn_row = None
        for box, values, selected in [
            (
                self.preset_box,
                self.settings.data["presets"],
                self.settings.data["preset"],
            ),
            (
                self.sound_box,
                self.settings.data["sets"],
                self.settings.data["sound_set"],
            ),
            (
                self.options_preset_box,
                self.settings.data["presets"],
                self.settings.data["preset"],
            ),
            (
                self.options_sound_box,
                self.settings.data["sets"],
                self.settings.data["sound_set"],
            ),
        ]:
            box.blockSignals(True)
            box.clear()
            box.addItems(values)
            box.setCurrentText(selected)
            box.blockSignals(False)
        self.populate_table()
        while self.pad_layout.count():
            item = self.pad_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.pad_buttons = []
        self.sounds.clear()
        for i, pad in enumerate(self.settings.pads):
            button = W.QPushButton(
                f'{pad["name"]}\nNote {pad["note"]}'
                + (f" • key {(i+1)%10}" if i < 10 else "")
            )
            button.setMinimumHeight(78)
            button.clicked.connect(lambda checked=False, index=i: self.simulate(index))
            self.pad_layout.addWidget(button, i // 5, i % 5)
            self.pad_buttons.append(button)
            path = self.sample_path(pad)
            if path:
                try:
                    array = (np.clip(read_sample(path), -1, 1) * 32767).astype(np.int16)
                    if pygame.mixer.get_init():
                        self.sounds[path] = pygame.sndarray.make_sound(array.copy())
                except Exception as exc:
                    self.log.appendPlainText(
                        f'Sample unavailable for {pad["name"]}: {exc}'
                    )

        for entries in (
            self.settings.data.get("layers", {})
            .get(self.settings.data["sound_set"], {})
            .values()
        ):
            for entry in entries:
                path = entry["sample"]
                try:
                    if path and pygame.mixer.get_init():
                        self.sounds[path] = pygame.sndarray.make_sound(
                            (np.clip(read_sample(path), -1, 1) * 32767)
                            .astype(np.int16)
                            .copy()
                        )
                except Exception as exc:
                    self.log.appendPlainText(f"Layer sample unavailable: {exc}")

    def sample_path(self, pad, velocity=127):
        return self.settings.sample(pad["id"], velocity)

    def populate_table(self):
        self.table.setRowCount(len(self.settings.pads))
        for r, p in enumerate(self.settings.pads):
            for c, value in enumerate(
                [
                    p["name"],
                    p["note"],
                    p["channel"],
                    p["device"],
                    p["gain"],
                    p["sensitivity"],
                    self.sample_path(p),
                ]
            ):
                item = W.QTableWidgetItem(str(value))
                if c == 6:
                    item.setText(Path(str(value)).name if value else "")
                    item.setToolTip(str(value))
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(r, c, item)
            volume = W.QSpinBox()
            volume.setRange(0, 200)
            volume.setSuffix(" %")
            volume.setSingleStep(5)
            volume.setValue(round(self.settings.sound_volume(p["id"]) * 100))
            volume.setToolTip(
                "Volume for this drum in the selected sound set. 0% mutes; 100% is original; changes save immediately."
            )
            volume.valueChanged.connect(
                lambda value, set_name=self.settings.data["sound_set"], pad_id=p[
                    "id"
                ]: self.save_sound_volume(set_name, pad_id, value)
            )
            self.table.setCellWidget(r, 7, volume)

    def save_sound_volume(self, set_name, pad_id, percent):
        self.settings.data.setdefault("sound_volumes", {}).setdefault(set_name, {})[
            pad_id
        ] = (percent / 100)
        self.settings.save()

    def select_preset(self, name):
        if name:
            self.settings.data["preset"] = name
            self.settings.save()
            self.refresh_settings()

    def select_sound(self, name):
        if name:
            self.settings.data["sound_set"] = name
            self.settings.save()
            self.refresh_settings()

    def new_preset(self):
        self.create_preset(duplicate=False)

    def duplicate_preset(self):
        self.create_preset(duplicate=True)

    def create_preset(self, duplicate=False):
        name, ok = W.QInputDialog.getText(self, "Preset", "New preset name")
        if not ok:
            return
        name = name.strip()
        if not name:
            return self.error("Enter a name.")
        if name in self.settings.data["presets"]:
            return self.error(
                f'A preset named "{name}" already exists. Select it from the dropdown to edit it.'
            )
        if name:
            self.settings.data["presets"][name] = (
                copy.deepcopy(self.settings.pads) if duplicate else []
            )
            self.select_preset(name)

    def delete_preset(self):
        if len(self.settings.data["presets"]) < 2:
            return self.error("Keep at least one preset.")
        del self.settings.data["presets"][self.settings.data["preset"]]
        self.select_preset(next(iter(self.settings.data["presets"])))

    def add_pad(self):
        self.settings.pads.append(
            dict(
                id=uuid.uuid4().hex,
                name="New pad",
                note=60,
                channel=0,
                device="*",
                gain=1.0,
                sensitivity=1.0,
            )
        )
        self.settings.save()
        self.refresh_settings()

    def remove_pad(self):
        row = self.table.currentRow()
        if row >= 0:
            self.settings.pads.pop(row)
            self.settings.save()
            self.refresh_settings()

    def learn(self):
        row = self.table.currentRow()
        if row < 0:
            return self.error("Select an input first.")
        self.learn_row = row
        self.last_hit.setText("Learning: strike the desired pad on your MIDI kit…")

    def save_table(self):
        old = copy.deepcopy(self.settings.data)
        try:
            for r, p in enumerate(self.settings.pads):
                values = [self.table.item(r, c).text().strip() for c in range(6)]
                p.update(
                    name=values[0],
                    note=int(values[1]),
                    channel=int(values[2]),
                    device=values[3] or "*",
                    gain=float(values[4]),
                    sensitivity=float(values[5]),
                )
            self.settings.save()
            self.refresh_settings()
        except Exception as exc:
            self.settings.data = old
            self.error(exc)

    def new_sound_set(self):
        name, ok = W.QInputDialog.getText(self, "Sound set", "New sound set name")
        if not ok:
            return
        name = name.strip()
        if not name:
            return self.error("Enter a name.")
        if name in self.settings.data["sets"]:
            return self.error(
                f'A sound set named "{name}" already exists. Select it from the dropdown to edit it.'
            )
        if name:
            self.settings.data["sets"][name] = {}
            self.settings.data.setdefault("layers", {})[name] = {}
            self.settings.data.setdefault("sound_volumes", {})[name] = {}
            self.select_sound(name)

    def delete_sound_set(self):
        if len(self.settings.data["sets"]) < 2:
            return self.error("Keep at least one sound set.")
        self.settings.data.get("layers", {}).pop(self.settings.data["sound_set"], None)
        self.settings.data.get("sound_volumes", {}).pop(
            self.settings.data["sound_set"], None
        )
        del self.settings.data["sets"][self.settings.data["sound_set"]]
        self.select_sound(next(iter(self.settings.data["sets"])))

    def assign_sample(self):
        row = self.table.currentRow()
        if row < 0:
            return self.error("Select an input first.")
        path, _ = W.QFileDialog.getOpenFileName(self, "Import WAV", "", "WAV (*.wav)")
        if path:
            try:
                imported = self.settings.import_sample(path)
                sound_set = self.settings.data["sound_set"]
                pad_id = self.settings.pads[row]["id"]
                self.settings.data["sets"][sound_set][pad_id] = imported
                # A single-WAV assignment replaces any velocity-layer assignment.
                # Otherwise the layers would continue to override the new sample.
                self.settings.data.get("layers", {}).get(sound_set, {}).pop(
                    pad_id, None
                )
                self.settings.save()
                self.refresh_settings()
                self.table.setCurrentCell(row, 6)
                self.statusBar().showMessage(
                    f"Assigned {Path(path).name} • ready to preview"
                )
            except Exception as exc:
                self.error(exc)

    def edit_layers(self):
        row = self.table.currentRow()
        if row < 0:
            return self.error("Select an input first.")
        pad = self.settings.pads[row]
        entries = copy.deepcopy(
            self.settings.data.get("layers", {})
            .get(self.settings.data["sound_set"], {})
            .get(pad["id"], [])
        )
        dialog = W.QDialog(self)
        dialog.setWindowTitle("Velocity layers • " + pad["name"])
        dialog.resize(650, 350)
        layout = W.QVBoxLayout(dialog)
        layout.addWidget(
            W.QLabel(
                "Each layer applies up to its maximum velocity. Last threshold must be 127.\nRemove all layers to use the regular WAV assignment."
            )
        )
        table = W.QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Maximum velocity", "Managed WAV"])
        table.horizontalHeader().setSectionResizeMode(W.QHeaderView.Interactive)
        table.setColumnWidth(0, 150)
        table.setColumnWidth(1, 430)
        layout.addWidget(table)

        def populate():
            table.setRowCount(len(entries))
            for r, entry in enumerate(entries):
                table.setItem(r, 0, W.QTableWidgetItem(str(entry["max_velocity"])))
                item = W.QTableWidgetItem(Path(entry["sample"]).name)
                item.setToolTip(entry["sample"])
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                table.setItem(r, 1, item)

        def add():
            path, _ = W.QFileDialog.getOpenFileName(
                dialog, "Layer WAV", "", "WAV (*.wav)"
            )
            if path:
                try:
                    for r, entry in enumerate(entries):
                        entry["max_velocity"] = int(table.item(r, 0).text())
                    entries.append(
                        dict(max_velocity=127, sample=self.settings.import_sample(path))
                    )
                    populate()
                except Exception as exc:
                    self.error(exc)

        def remove():
            r = table.currentRow()
            if r >= 0:
                entries.pop(r)
                populate()

        def save():
            old = copy.deepcopy(self.settings.data)
            try:
                for r, entry in enumerate(entries):
                    entry["max_velocity"] = int(table.item(r, 0).text())
                entries.sort(key=lambda entry: entry["max_velocity"])
                layers = self.settings.data.setdefault("layers", {}).setdefault(
                    self.settings.data["sound_set"], {}
                )
                if entries:
                    layers[pad["id"]] = entries
                else:
                    layers.pop(pad["id"], None)
                self.settings.save()
                dialog.accept()
                self.refresh_settings()
            except Exception as exc:
                self.settings.data = old
                self.error(exc)

        buttons = W.QHBoxLayout()
        layout.addLayout(buttons)
        self.button("Add WAV layer", add, buttons)
        self.button("Remove layer", remove, buttons)
        self.button("Save layers", save, buttons)
        populate()
        dialog.exec()

    def load_backing(self):
        if (
            self.timeline.state != "idle"
            or self.busy
            or getattr(self, "counting", False)
        ):
            return
        path, _ = W.QFileDialog.getOpenFileName(
            self, "Backing track", "", "PCM WAV (*.wav)"
        )
        if path:
            try:
                self.backing_path = self.settings.import_sample(path)
                self.backing_label.setText(Path(path).name)
                self.save_preferences()
            except Exception as exc:
                self.error(exc)

    def clear_backing(self):
        if (
            self.timeline.state != "idle"
            or self.busy
            or getattr(self, "counting", False)
        ):
            return
        self.backing_path = ""
        self.backing_label.setText("No backing track")
        self.save_preferences()

    def preview_sample(self):
        if self.table.currentRow() >= 0:
            self.simulate(self.table.currentRow())

    def export_bundle(self):
        path, _ = W.QFileDialog.getSaveFileName(
            self, "Export portable presets", "presets.zip", "ZIP (*.zip)"
        )
        if path:
            try:
                self.settings.export_bundle(path)
            except Exception as exc:
                self.error(exc)

    def import_bundle(self):
        if (
            W.QMessageBox.question(
                self,
                "Import",
                "Replace current presets and sound sets? Export them first to keep a backup.",
            )
            != W.QMessageBox.Yes
        ):
            return
        path, _ = W.QFileDialog.getOpenFileName(
            self, "Import portable presets", "", "ZIP (*.zip)"
        )
        if path:
            try:
                self.settings.import_bundle(path)
                self.restore_preferences()
                self.refresh_settings()
            except Exception as exc:
                self.error(exc)

    def refresh_devices(self):
        if (
            getattr(self, "scanning", False)
            or self.timeline.state != "idle"
            or self.busy
        ):
            return
        self.scanning = True
        self.statusBar().showMessage("Scanning MIDI devices and cameras…")

        def work():
            devices = []
            cameras = []
            errors = []
            try:
                devices = mido.get_input_names()
            except Exception as exc:
                errors.append("MIDI: " + str(exc))
            for i in range(5):
                cap = cv2.VideoCapture(
                    i,
                    (
                        cv2.CAP_DSHOW
                        if __import__("sys").platform == "win32"
                        else cv2.CAP_ANY
                    ),
                )
                if cap.isOpened():
                    cameras.append(i)
                cap.release()
            self.devices_done.emit(devices, cameras, "; ".join(errors))

        threading.Thread(target=work, daemon=True).start()

    def on_devices(self, devices, cameras, error):
        self.scanning = False
        saved = self.settings.data.get("midi_device", "")
        self.midi_box.blockSignals(True)
        self.midi_box.clear()
        self.midi_box.addItem("Simulation only", "")
        self.midi_box.addItems(devices)
        if saved in devices:
            self.midi_box.setCurrentText(saved)
        self.midi_box.blockSignals(False)
        self.connect_midi()
        self.camera_box.blockSignals(True)
        self.camera_box.clear()
        for index in cameras:
            self.camera_box.addItem(f"Camera {index}", index)
        saved_camera = self.settings.data.get("camera_index", 0)
        for row in range(self.camera_box.count()):
            if self.camera_box.itemData(row) == saved_camera:
                self.camera_box.setCurrentIndex(row)
        self.camera_box.blockSignals(False)
        self.change_camera()
        self.statusBar().showMessage(
            error or f"{len(devices)} MIDI inputs • {len(cameras)} cameras"
        )

    def connect_midi(self):
        if self.port:
            self.port.close()
            self.port = None
        name = self.midi_box.currentText()
        if name == "Simulation only":
            self.settings.data["midi_device"] = ""
            self.settings.save()
        if name and name != "Simulation only":
            try:
                self.port = mido.open_input(
                    name,
                    callback=lambda msg, device=name: self.events.put((device, msg)),
                )
                self.settings.data["midi_device"] = name
                self.settings.save()
            except Exception as exc:
                self.error(exc)

    def change_camera(self):
        selected = self.camera_box.currentData()
        if selected is not None:
            self.settings.data["camera_index"] = selected
            self.settings.save()
        if self.timeline.state != "idle":
            return
        if self.camera:
            self.camera.release()
            self.camera = None
        if self.preview_check.isChecked() and self.camera_box.currentData() is not None:
            self.camera = CameraStream(self.camera_box.currentData())
            if not self.camera.isOpened():
                self.camera.release()
                self.camera = None
                self.error("Camera could not be opened.")
        if self.camera is None:
            self.preview.clear()
            self.preview.setText("Camera off")

    def simulate(self, index):
        if index < len(self.settings.pads):
            p = self.settings.pads[index]
            self.hit(
                p,
                "Keyboard / pad",
                max(1, p["channel"]),
                p["note"],
                self.velocity.value(),
            )

    def midi_event(self, device, msg):
        self.last_hit.setText(f"{device} • {msg}")
        if msg.type == "control_change" and msg.control == 4:
            self.hihat = msg.value
        if msg.type == "polytouch" and msg.value > 0:
            for p in self.settings.pads:
                if (
                    p["note"] == msg.note
                    and p["channel"] in (0, msg.channel + 1)
                    and p["device"] in ("*", device)
                ):
                    channel = self.channels.get(p["id"])
                    if channel:
                        channel.fadeout(40)
                    self.timeline.choke(p["id"])
        if msg.type != "note_on" or msg.velocity == 0:
            return
        if self.learn_row is not None:
            r = self.learn_row
            self.learn_row = None
            for c, value in [(1, msg.note), (2, msg.channel + 1), (3, device)]:
                self.table.item(r, c).setText(str(value))
            self.last_hit.setText(
                "Learned. Click Save mappings to keep this assignment."
            )
            return
        matched = False
        for p in self.settings.pads:
            if (
                p["note"] == msg.note
                and p["channel"] in (0, msg.channel + 1)
                and p["device"] in ("*", device)
            ):
                target = p
                if msg.note == 42 and self.hihat < 64:
                    target = next(
                        (pad for pad in self.settings.pads if pad["note"] == 46), p
                    )
                self.hit(target, device, msg.channel + 1, msg.note, msg.velocity)
                matched = True
        if not matched:
            self.log.appendPlainText(
                f"Unmapped: {device} • channel {msg.channel+1} • note {msg.note} • velocity {msg.velocity}"
            )

    def hit(self, pad, device, channel, note, velocity):
        velocity = min(127, max(1, round(velocity * pad["sensitivity"])))
        gain = (
            pad["gain"]
            * self.settings.sound_volume(pad["id"])
            * self.volume.value()
            / 100
        )
        if pad["note"] == 42:
            for opened in self.settings.pads:
                if opened["note"] == 46:
                    playing = self.channels.get(opened["id"])
                    if playing:
                        playing.fadeout(40)
                    self.timeline.choke(opened["id"])
        sample_path = self.sample_path(pad, velocity)
        sound = self.sounds.get(sample_path)
        if sound:
            level = gain * velocity / 127
            if level > 1:
                data = pygame.sndarray.array(sound).astype(np.float32) * level
                playing = pygame.sndarray.make_sound(
                    np.clip(data, -32768, 32767).astype(np.int16)
                ).play()
            else:
                playing = sound.play()
                if playing:
                    playing.set_volume(level)
            if playing:
                self.channels[pad["id"]] = playing
        index = self.settings.pads.index(pad)
        button = self.pad_buttons[index]
        button.setStyleSheet("background:#bc8634;color:#10151d")
        QtCore.QTimer.singleShot(
            100,
            lambda b=button: (
                b.setStyleSheet("") if __import__("shiboken6").isValid(b) else None
            ),
        )
        self.log.appendPlainText(
            f'{pad["name"]} • {device} • ch {channel} • note {note} • velocity {velocity}'
        )
        self.timeline.hit(
            pad=pad["name"],
            pad_id=pad["id"],
            device=device,
            channel=channel,
            note=note,
            velocity=velocity,
            sample=sample_path,
            gain=gain,
        )

    def choose_folder(self):
        folder = W.QFileDialog.getExistingDirectory(
            self,
            "Output folder",
            self.settings.data.get("output_folder", str(Path.home())),
        )
        if folder:
            self.settings.data["output_folder"] = folder
            self.settings.save()

    def start_record(self):
        if (
            self.busy
            or self.timeline.state != "idle"
            or getattr(self, "counting", False)
            or getattr(self, "scanning", False)
        ):
            return
        if (
            self.dirty_take
            and W.QMessageBox.question(
                self, "Unsaved take", "Discard the current take and record a new one?"
            )
            != W.QMessageBox.Yes
        ):
            return
        self.bpm_snapshot = self.bpm.value()
        self.beats_snapshot = self.beats.value()
        if self.record_video.isChecked():
            self.preview_check.setChecked(True)
            if self.camera is None:
                return self.error(
                    "Select an available camera on Test before recording video."
                )
        if self.count_in.isChecked():
            self.counting = True
            self.set_take_controls(False)
            self.count_deadline = (
                time.monotonic() + self.beats.value() * 60 / self.bpm.value()
            )
            self.last_click = -1
        else:
            self.begin_take()

    def begin_take(self):
        self.counting = False
        self.video_path = None
        if self.record_video.isChecked():
            ok, frame = self.camera.read()
            if not ok:
                self.set_take_controls(True)
                return self.error("Camera returned no frame.")
            self.video_path = Path(self.temp.name) / f"{uuid.uuid4().hex}.mp4"
            h, w = frame.shape[:2]
            self.writer = cv2.VideoWriter(
                str(self.video_path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h)
            )
            if not self.writer.isOpened():
                self.writer.release()
                self.writer = None
                self.set_take_controls(True)
                return self.error("Camera video encoder is unavailable.")
            self.last_frame = frame
            self.video_frames = 0
        self.timeline.start()
        self.last_click = -1
        self.dirty_take = True
        if self.backing_path:
            if self.include_backing.isChecked():
                self.timeline.backing = self.backing_path
                self.timeline.backing_gain = self.backing_gain.value() / 100
            if pygame.mixer.get_init():
                pygame.mixer.music.load(self.backing_path)
                pygame.mixer.music.set_volume(self.backing_gain.value() / 100)
                pygame.mixer.music.play()
        self.set_take_controls(False)

    def set_take_controls(self, enabled):
        for widget in (
            self.preset_box,
            self.sound_box,
            self.midi_box,
            self.camera_box,
            self.preview_check,
            self.record_video,
            self.bpm,
            self.beats,
            self.count_in,
            self.refresh_button,
            self.backing_button,
            self.backing_gain,
            self.include_backing,
        ):
            widget.setEnabled(enabled)
        self.tabs.setTabEnabled(2, enabled)

    def pause_record(self):
        self.timeline.pause()
        if pygame.mixer.get_init():
            if self.timeline.state == "paused":
                pygame.mixer.music.pause()
            elif self.timeline.state == "recording":
                pygame.mixer.music.unpause()

    def stop_record(self):
        self.counting = False
        if self.timeline.state == "idle":
            self.set_take_controls(True)
            return
        self.flush_video()
        self.timeline.stop()
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
        if self.writer:
            self.writer.release()
            self.writer = None
        self.set_take_controls(True)

    def flush_video(self):
        if (
            self.writer
            and self.timeline.state == "recording"
            and self.last_frame is not None
        ):
            target = math.ceil(self.timeline.elapsed() * 30)
            # Duplicate the last frame during slow camera reads to retain the audio clock.
            while self.video_frames < target:
                self.writer.write(self.last_frame)
                self.video_frames += 1

    def click(self, accent=False):
        if not pygame.mixer.get_init():
            return
        x = np.arange(2205) / 44100
        tone = (
            np.sin(x * 2 * np.pi * (1500 if accent else 1000)) * np.exp(-x * 80) * 5000
        ).astype(np.int16)
        pygame.sndarray.make_sound(np.stack([tone, tone], axis=1)).play()

    def tick(self):
        for _ in range(200):
            try:
                device, msg = self.events.get_nowait()
            except queue.Empty:
                break
            self.midi_event(device, msg)
        if getattr(self, "counting", False):
            left = self.count_deadline - time.monotonic()
            self.elapsed_label.setText(
                f"Count-in: {max(1,math.ceil(left*self.bpm.value()/60))}"
            )
            beat = int(
                (self.beats.value() * 60 / self.bpm.value() - left)
                * self.bpm.value()
                / 60
            )
            if beat != self.last_click:
                self.click(beat == 0)
                self.last_click = beat
            if left <= 0:
                self.begin_take()
        else:
            self.elapsed_label.setText(
                f"{self.timeline.state.title()} • {self.timeline.elapsed():.2f} s • {len(self.timeline.hits)} hits"
            )
        if self.metronome.isChecked() and self.timeline.state == "recording":
            beat = int(self.timeline.elapsed() * self.bpm.value() / 60)
            if beat != self.last_click:
                self.click(beat % self.beats.value() == 0)
                self.last_click = beat
        if self.camera is not None:
            ok, frame = self.camera.read()
            if ok:
                self.last_frame = frame
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = QtGui.QImage(
                    rgb.data,
                    rgb.shape[1],
                    rgb.shape[0],
                    rgb.strides[0],
                    QtGui.QImage.Format_RGB888,
                ).copy()
                pix = QtGui.QPixmap.fromImage(image).scaled(
                    640, 280, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
                )
                self.preview.setPixmap(pix)
                self.record_preview.setPixmap(pix)
            elif self.timeline.state == "recording" and self.writer:
                self.statusBar().showMessage(
                    "Camera frame unavailable; holding last frame to preserve timing."
                )
            self.flush_video()
        active = self.timeline.state != "idle" or getattr(self, "counting", False)
        self.record_button.setEnabled(
            not active and not self.busy and not getattr(self, "scanning", False)
        )
        self.pause_button.setEnabled(self.timeline.state != "idle")
        self.stop_button.setEnabled(active)
        for button in self.export_buttons:
            button.setEnabled(not active and not self.busy and self.dirty_take)

    def export(self, kind):
        if self.timeline.state != "idle" or self.busy:
            return
        if kind == "MP4" and not self.video_path:
            return self.error(
                "This take has no video. Export WAV or enable video for your next take."
            )
        suffix = {
            "WAV": "wav",
            "MP4": "mp4",
            "CSV": "csv",
            "JSON": "json",
            "MIDI": "mid",
        }[kind]
        default = (
            Path(self.settings.data.get("output_folder", str(Path.home())))
            / f'take-{time.strftime("%Y%m%d-%H%M%S")}.{suffix}'
        )
        target, _ = W.QFileDialog.getSaveFileName(
            self, "Save recording", str(default), f"{kind} (*.{suffix})"
        )
        if not target:
            return
        self.busy = True
        self.set_take_controls(False)
        self.statusBar().showMessage("Exporting…")
        snapshot = copy.deepcopy(self.timeline)
        bpm = getattr(self, "bpm_snapshot", 120)
        beats = getattr(self, "beats_snapshot", 4)
        video = self.video_path

        def work():
            try:
                # Render to a temporary neighbor, then replace only a complete output.
                temporary = Path(target).with_name(
                    "." + Path(target).stem + "-" + uuid.uuid4().hex + "." + suffix
                )
                try:
                    if kind == "WAV":
                        snapshot.render(temporary)
                    elif kind == "MP4":
                        with tempfile.TemporaryDirectory() as temp:
                            audio = Path(temp) / "audio.wav"
                            snapshot.render(audio)
                            mux_video(video, audio, temporary)
                    elif kind == "MIDI":
                        snapshot.export_midi(temporary, bpm, beats)
                    else:
                        snapshot.export_events(temporary, bpm, beats)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                self.export_done.emit("Saved " + target)
            except Exception as exc:
                self.export_done.emit("Export failed: " + str(exc))

        threading.Thread(target=work, daemon=True).start()

    def on_export_done(self, message):
        self.busy = False
        self.set_take_controls(True)
        self.statusBar().showMessage(message)
        if message.startswith("Export failed"):
            self.error(message)

    def closeEvent(self, event):
        if self.busy:
            self.error("Wait for the current export to finish.")
            event.ignore()
            return
        if (
            self.dirty_take
            and W.QMessageBox.question(
                self,
                "Close",
                "Close and discard the in-memory take? Saved exports are retained.",
            )
            != W.QMessageBox.Yes
        ):
            event.ignore()
            return
        self.stop_record()
        if self.port:
            self.port.close()
        if self.camera:
            self.camera.release()
        pygame.mixer.quit()
        self.temp.cleanup()
        event.accept()


def main():
    application = W.QApplication([])
    application.setStyle("Fusion")
    application.setStyleSheet(
        "QWidget {font-size:13px;} QMainWindow,QWidget {background:#18202b;color:#edf1f7;} QPushButton {background:#2c3d50;padding:9px;border-radius:5px;} QPushButton:disabled {color:#76808a;} QLineEdit,QComboBox,QSpinBox,QPlainTextEdit,QTableWidget {background:#222e3b;} QTabBar::tab {padding:13px 28px;} QTabBar::tab:selected {background:#38536a;}"
    )
    try:
        window = Window()
    except Exception as exc:
        W.QMessageBox.critical(None, "Startup failed", str(exc))
        return 1
    window.show()
    return application.exec()
