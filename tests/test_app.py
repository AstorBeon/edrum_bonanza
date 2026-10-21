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
