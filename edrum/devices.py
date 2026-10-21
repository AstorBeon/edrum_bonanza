"""Device adapters keep blocking camera reads off the GUI/MIDI event loop."""

import threading
import time
import cv2


class CameraStream:
    def __init__(self, index):
        self.capture = cv2.VideoCapture(index)
        self.opened = self.capture.isOpened()
        self.frame = None
        self.updated = 0.0
        self.stopped = threading.Event()
        self.thread = None
        if self.opened:
            self.thread = threading.Thread(target=self._read, daemon=True)
            self.thread.start()
        else:
            self.capture.release()

    def _read(self):
        try:
            while not self.stopped.is_set():
                ok, frame = self.capture.read()
                if ok:
                    self.frame = frame
                    self.updated = time.monotonic()
                else:
                    self.stopped.wait(0.02)
        finally:
            self.capture.release()

    def isOpened(self):
        return self.opened and not self.stopped.is_set()

    def read(self):
        frame = self.frame
        return frame is not None and time.monotonic() - self.updated < 1.0, frame

    def release(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=0.5)
