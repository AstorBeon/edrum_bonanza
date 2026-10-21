import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
from PySide6 import QtWidgets
import mido
from edrum.core import Settings
from edrum import app


def test_desktop_simulation_learn_and_record(tmp_path, monkeypatch):
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(app, "Settings", lambda: Settings(tmp_path))
    monkeypatch.setattr(app.Window, "refresh_devices", lambda self: None)
    window = app.Window()
    assert window.tabs.count() == 4
    window.start_record()
    window.simulate(0)
    window.pause_record()
    window.simulate(1)
    assert len(window.timeline.hits) == 1
    window.pause_record()
    window.simulate(1)
    window.stop_record()
    assert len(window.timeline.hits) == 2
    window.table.selectRow(0)
    window.learn()
    window.midi_event(
        "Mock kit", mido.Message("note_on", channel=9, note=40, velocity=90)
    )
    window.save_table()
    assert window.settings.pads[0]["note"] == 40
    assert window.settings.pads[0]["channel"] == 10
    assert window.settings.pads[0]["device"] == "Mock kit"
    target = tmp_path / "gui-take.wav"
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(target), "WAV"),
    )
    window.export("WAV")
    from PySide6.QtTest import QTest
    import time

    deadline = time.monotonic() + 5
    while window.busy and time.monotonic() < deadline:
        QTest.qWait(10)
    assert not window.busy
    assert target.stat().st_size > 44
    window.dirty_take = False
    window.close()
    application.processEvents()


def test_video_pause_uses_same_clock(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(app, "Settings", lambda: Settings(tmp_path))
    monkeypatch.setattr(app.Window, "refresh_devices", lambda self: None)

    class Camera:
        def read(self):
            return True, np.full((120, 160, 3), 100, np.uint8)

        def release(self):
            pass

    window = app.Window()
    window.preview_check.setChecked(True)
    window.camera = Camera()
    window.record_video.setChecked(True)
    now = [0.0]
    window.timeline.clock = lambda: now[0]
    window.start_record()
    now[0] = 1
    window.tick()
    assert window.video_frames == 30
    window.pause_record()
    now[0] = 5
    window.tick()
    assert window.video_frames == 30
    window.pause_record()
    now[0] = 6
    window.tick()
    window.stop_record()
    assert window.timeline.duration == 2
    capture = cv2.VideoCapture(str(window.video_path))
    assert capture.get(cv2.CAP_PROP_FRAME_COUNT) == 60
    capture.release()
    window.dirty_take = False
    window.close()
    application.processEvents()


def test_assign_wav_replaces_layers_and_loads_named_sample(tmp_path, monkeypatch):
    from pathlib import Path
    import wave
    import numpy as np
    import pygame

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(app, "Settings", lambda: Settings(tmp_path / "settings"))
    monkeypatch.setattr(app.Window, "refresh_devices", lambda self: None)
    source = tmp_path / "New snare.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
        output.writeframes(np.full((4410, 2), 2000, np.int16).tobytes())
    window = app.Window()
    pad_id = window.settings.pads[1]["id"]
    old = window.sample_path(window.settings.pads[1])
    window.settings.data["layers"] = {
        "Default": {pad_id: [dict(max_velocity=127, sample=old)]}
    }
    window.refresh_settings()
    window.table.selectRow(1)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "WAV")
    )
    window.assign_sample()
    assigned = window.sample_path(window.settings.pads[1], 50)
    assert Path(assigned).name == source.name
    assert Path(assigned).read_bytes() == source.read_bytes()
    assert pad_id not in window.settings.data["layers"]["Default"]
    assert assigned in window.sounds
    assert np.max(pygame.sndarray.array(window.sounds[assigned])) == 1999
    assert window.table.item(1, 6).text() == source.name
    assert window.table.item(1, 6).toolTip() == assigned
    header = window.table.horizontalHeader()
    assert header.sectionResizeMode(6) == QtWidgets.QHeaderView.Interactive
    window.table.setColumnWidth(6, 360)
    assert window.table.columnWidth(6) == 360
    window.start_record()
    window.simulate(1)
    window.stop_record()
    assert window.timeline.hits[0]["sample"] == assigned
    restored = Settings(tmp_path / "settings")
    assert restored.sample(pad_id, 50) == assigned
    window.dirty_take = False
    window.close()
    application.processEvents()
