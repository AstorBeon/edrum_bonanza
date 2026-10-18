#!/usr/bin/env python3
"""
E-Drum Kit Studio Bonanza
-----------------
A desktop e-drum MIDI tester, recorder, and sample-set manager.

Install dependencies:
    pip install mido python-rtmidi pygame opencv-python

Run:
    python edrum_studio.py

The application stores its configuration inside this Python file so settings,
MIDI mappings, sample-set names, and pad names survive a restart.
"""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import platform
import threading
import queue
from PIL import Image, ImageTk


import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Optional runtime dependencies are handled gracefully.
try:
    import mido
except Exception:
    mido = None

try:
    import pygame
    from pygame import mixer
except Exception:
    pygame = None
    mixer = None

try:
    import cv2
except Exception:
    cv2 = None


# ======================================================================
# PERSISTENT CONFIGURATION
# ======================================================================
# The Options window rewrites this dictionary back into this same .py file.
# Keep this block valid Python. Do not manually put executable code in it.
CONFIG = {'default_midi_device': '', 'default_set': 'Default', 'camera_index': 0, 'pads': {'Kick': {'note': 36, 'name': 'Kick'}, 'Snare': {'note': 38, 'name': 'Snare'}, 'Hi-Hat': {'note': 42, 'name': 'Hi-Hat'}, 'Open Hi-Hat': {'note': 46, 'name': 'Open Hi-Hat'}, 'Tom 1': {'note': 48, 'name': 'Tom 1'}, 'Tom 2': {'note': 45, 'name': 'Tom 2'}, 'Tom 3': {'note': 43, 'name': 'Tom 3'}, 'Crash': {'note': 49, 'name': 'Crash'}, 'Ride': {'note': 51, 'name': 'Ride'}, 'China': {'note': 52, 'name': 'China'}}, 'sets': {'Default': {'Kick': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\kick.wav', 'Snare': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\snare.wav', 'Hi-Hat': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\hi-hat.wav', 'Open Hi-Hat': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\open_hi-hat.wav', 'Tom 1': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\tom_1.wav', 'Tom 2': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\tom_2.wav', 'Tom 3': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\tom_3.wav', 'Crash': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\crash.wav', 'Ride': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\ride.wav', 'China': 'C:\\Users\\macie\\PycharmProjects\\edrum_bonanza\\default_sounds\\china.wav'}}}


# ======================================================================
# DATA / HELPERS
# ======================================================================
PAD_ORDER = [
    "Kick", "Snare", "Hi-Hat", "Open Hi-Hat", "Tom 1", "Tom 2",
    "Tom 3", "Crash", "Ride", "China"
]

APP_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = APP_DIR / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)

class ConnectionLoader:
    def __init__(self, app):
        self.app = app
        self.results = queue.Queue()
        self.running = False

    def start(self):
        if self.running:
            return

        self.running = True

        threading.Thread(
            target=self._worker,
            daemon=True
        ).start()

    def _worker(self):
        try:
            self.results.put(("status", "Detecting MIDI devices..."))

            midi_devices = list_midi_inputs()

            self.results.put(("midi", midi_devices))

            self.results.put(("status", "Detecting cameras..."))

            cameras = CameraManager.list_cameras()

            self.results.put(("cameras", cameras))

            self.results.put(("done", None))

        except Exception as e:
            self.results.put(("error", str(e)))
        finally:
            self.running = False

class CameraManager:
    def __init__(self):
        self.cap = None
        self.cached_cameras = None
        self.camera_index = None
        self.recording_writer = None
        self.recording_path = None
        self.last_frame = None
        self.last_record_time = 0.0
        self.record_fps = 30.0

    def get_cameras(self, refresh=False):
        if refresh or self.cached_cameras is None:
            self.cached_cameras = self.list_cameras()

        return self.cached_cameras


    @staticmethod
    def list_cameras(max_cameras=5):
        cameras = []

        if cv2 is None:
            return cameras

        system = platform.system()

        if system == "Windows":
            backend = cv2.CAP_DSHOW
        elif system == "Darwin":
            backend = cv2.CAP_AVFOUNDATION
        else:
            backend = cv2.CAP_V4L2

        for index in range(max_cameras):
            cap = None

            try:
                cap = cv2.VideoCapture(index, backend)

                if cap.isOpened():
                    cameras.append(
                        (index, f"Camera {index}")
                    )
            except Exception:
                pass
            finally:
                if cap is not None:
                    cap.release()

        return cameras

    def open(self, index):
        self.close()

        if cv2 is None:
            return False

        system = platform.system()

        if system == "Windows":
            backend = cv2.CAP_DSHOW
        elif system == "Darwin":
            backend = cv2.CAP_AVFOUNDATION
        else:
            backend = cv2.CAP_V4L2

        self.cap = cv2.VideoCapture(int(index), backend)

        if not self.cap.isOpened():
            self.cap.release()
            self.cap = None
            return False

        self.camera_index = int(index)
        return True
    def read(self):
        if self.cap is None:
            return None

        ok, frame = self.cap.read()

        if not ok:
            return None

        self.last_frame = frame

        if self.recording_writer is not None:
            now = time.monotonic()

            if now - self.last_record_time >= 1 / self.record_fps:
                self.recording_writer.write(frame)
                self.last_record_time = now

        return frame

    def start_recording(self, path):
        if self.cap is None:
            return False

        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if width <= 0 or height <= 0:
            return False

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        self.recording_writer = cv2.VideoWriter(
            str(path),
            fourcc,
            self.record_fps,
            (width, height)
        )

        if not self.recording_writer.isOpened():
            self.recording_writer.release()
            self.recording_writer = None
            return False

        self.recording_path = Path(path)
        self.last_record_time = 0.0
        return True

    def stop_recording(self):
        if self.recording_writer is not None:
            self.recording_writer.release()
            self.recording_writer = None

    def close(self):
        self.stop_recording()

        if self.cap is not None:
            self.cap.release()
            self.cap = None

        self.camera_index = None

class CameraPreview(ttk.LabelFrame):
    def __init__(self, parent, camera_manager, title="Camera Preview"):
        super().__init__(parent, text=title, padding=10)
        self.manager = camera_manager
        self.running = False
        self.after_id = None
        self.photo = None

        self.camera_var = tk.StringVar()

        self.camera_box = ttk.Combobox(
            self,
            textvariable=self.camera_var,
            state="readonly",
            width=30
        )
        self.camera_box.pack(fill="x", pady=5)

        self.camera_box.bind(
            "<<ComboboxSelected>>",
            self.change_camera
        )

        self.preview = tk.Label(
            self,
            text="Camera disabled",
            bg="#15171a",
            fg="white",
            anchor="center"
        )

        self.preview.pack(
            fill="both",
            expand=True,
            padx=5,
            pady=10
        )

        self.preview_width = 480
        self.preview_height = 360

        ttk.Button(
            self,
            text="Refresh Cameras",
            command=self.refresh_cameras
        ).pack(fill="x")

        self.set_camera_list(
            self.manager.cached_cameras or []
        )

    def set_camera_list(self, cameras):
        values = [
            f"{index}: {name}"
            for index, name in cameras
        ]

        self.camera_box["values"] = values

        saved_index = CONFIG.get("camera_index", 0)

        selected = next(
            (
                value for value in values
                if value.startswith(f"{saved_index}:")
            ),
            values[0] if values else ""
        )

        self.camera_var.set(selected)

    def refresh_cameras(self):
        self.camera_box.configure(state="disabled")

        def worker():
            cameras = CameraManager.list_cameras()

            self.manager.cached_cameras = cameras

            # Queue the result; never update Tkinter from this thread.
            self._refresh_results.put(cameras)

        if not hasattr(self, "_refresh_results"):
            self._refresh_results = queue.Queue()

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

        self.after(100, self.check_camera_refresh)

    def check_camera_refresh(self):
        try:
            cameras = self._refresh_results.get_nowait()
        except queue.Empty:
            if self.winfo_exists():
                self.after(100, self.check_camera_refresh)
            return

        self.set_camera_list(cameras)
        self.camera_box.configure(state="readonly")

    def get_selected_index(self):
        try:
            return int(self.camera_var.get().split(":")[0])
        except (ValueError, IndexError):
            return None

    def change_camera(self, event=None):
        self.stop()
        #self.start()

    def set_camera_list(self, cameras):
        values = [
            f"{index}: {name}"
            for index, name in cameras
        ]

        self.camera_box["values"] = values

        if not values:
            self.camera_var.set("")
            return

        saved_index = CONFIG.get("camera_index", 0)

        selected = next(
            (
                value for value in values
                if value.startswith(f"{saved_index}:")
            ),
            values[0]
        )

        self.camera_var.set(selected)

    def start(self):
        index = self.get_selected_index()

        if index is None:
            self.preview.configure(
                text="No camera detected",
                image=""
            )
            return

        if not self.manager.open(index):
            self.preview.configure(
                text=f"Cannot open camera {index}",
                image=""
            )
            return

        self.running = True
        self.update_frame()

    def update_frame(self):
        if not self.running:
            return

        frame = self.manager.read()

        if frame is not None:
            from PIL import Image, ImageTk

            # Convert OpenCV BGR image to RGB
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            image = Image.fromarray(frame)

            # Get the full original camera resolution
            original_width, original_height = image.size

            # Get current preview widget size
            available_width = self.preview.winfo_width()
            available_height = self.preview.winfo_height()

            # Fallback before Tkinter has calculated layout
            if available_width < 10:
                available_width = 480

            if available_height < 10:
                available_height = 360

            # Scale the COMPLETE image to fit inside the preview
            scale = min(
                available_width / original_width,
                available_height / original_height
            )

            new_width = max(1, int(original_width * scale))
            new_height = max(1, int(original_height * scale))

            # Resize while preserving the original aspect ratio
            image = image.resize(
                (new_width, new_height),
                Image.Resampling.LANCZOS
            )

            self.photo = ImageTk.PhotoImage(image)

            self.preview.configure(
                image=self.photo,
                text=""
            )

        self.after_id = self.after(33, self.update_frame)
    def stop(self):
        self.running = False

        if self.after_id is not None:
            self.after_cancel(self.after_id)
            self.after_id = None

        self.manager.close()

        self.preview.configure(
            image="",
            text="Camera disabled"
        )
        self.photo = None


def save_config_to_this_script() -> None:
    """Rewrite only the CONFIG block in this script."""
    path = Path(__file__)
    text = path.read_text(encoding="utf-8")
    start = text.index("CONFIG = ")
    marker = "\n\n\n# ======================================================================\n# DATA / HELPERS"
    end = text.index(marker, start)
    new_block = "CONFIG = " + repr(CONFIG)
    path.write_text(text[:start] + new_block + text[end:], encoding="utf-8")


def list_midi_inputs() -> List[str]:
    if mido is None:
        return []
    try:
        return list(mido.get_input_names())
    except Exception:
        return []


@staticmethod
def list_cameras(max_cameras=4):
    cameras = []

    if cv2 is None:
        return cameras

    import platform

    system = platform.system()

    if system == "Windows":
        backend = cv2.CAP_DSHOW
    elif system == "Darwin":
        backend = cv2.CAP_AVFOUNDATION
    else:
        backend = cv2.CAP_V4L2

    for index in range(max_cameras):
        try:
            cap = cv2.VideoCapture(index, backend)

            if cap.isOpened():
                cameras.append(
                    (index, f"Camera {index}")
                )

            cap.release()

        except Exception:
            pass

    return cameras
def list_midi_outputs() -> List[str]:
    if mido is None:
        return []
    try:
        return list(mido.get_output_names())
    except Exception:
        return []


def find_pad_for_note(note: int) -> Optional[str]:
    for pad, data in CONFIG["pads"].items():
        if int(data.get("note", -1)) == int(note):
            return pad
    return None


def wav_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


def default_tone_wav(path: Path, kind: str, duration: float = 0.22) -> None:
    """Create a simple PCM WAV used when a pad has no sample."""
    rate = 44100
    frames = int(rate * duration)
    path.parent.mkdir(parents=True, exist_ok=True)

    freq_map = {
        "Kick": 72, "Snare": 180, "Hi-Hat": 6500, "Open Hi-Hat": 5000,
        "Tom 1": 150, "Tom 2": 125, "Tom 3": 105,
        "Crash": 3200, "Ride": 2200, "China": 1700
    }
    freq = freq_map.get(kind, 220)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)

        data = bytearray()
        for i in range(frames):
            t = i / rate
            env = max(0.0, 1.0 - t / duration)
            if "Hi-Hat" in kind or kind in ("Crash", "Ride", "China"):
                # Deterministic pseudo-noise, shaped by a short decay.
                n = math.sin(i * 12.9898) * 43758.5453
                noise = (n - math.floor(n)) * 2.0 - 1.0
                sample = noise * env * 0.42
            elif kind == "Kick":
                f = freq * (1.0 - min(t / duration, 1.0) * 0.55)
                sample = math.sin(2 * math.pi * f * t) * env * 0.85
            else:
                sample = (
                    math.sin(2 * math.pi * freq * t) * env * 0.55
                    + math.sin(2 * math.pi * freq * 1.7 * t) * env * 0.20
                )
            data.extend(struct.pack("<h", int(max(-1, min(1, sample)) * 32767)))
        wf.writeframes(data)


def ensure_default_set() -> None:
    CONFIG.setdefault("sets", {})
    CONFIG["sets"].setdefault("Default", {})
    default_dir = APP_DIR / "default_sounds"
    default_dir.mkdir(exist_ok=True)

    for pad in PAD_ORDER:
        current = CONFIG["sets"]["Default"].get(pad, "")
        if not current or not Path(current).exists():
            p = default_dir / (pad.lower().replace(" ", "_") + ".wav")
            if not p.exists():
                default_tone_wav(p, pad)
            CONFIG["sets"]["Default"][pad] = str(p)


ensure_default_set()


class SamplePlayer:
    def __init__(self):
        self.enabled = mixer is not None
        self.sounds: Dict[Tuple[str, str], object] = {}
        if self.enabled:
            try:
                mixer.init()
            except Exception:
                self.enabled = False

    def load_set(self, set_name: str) -> None:
        if not self.enabled:
            return
        for pad in PAD_ORDER:
            key = (set_name, pad)
            path = CONFIG["sets"].get(set_name, {}).get(pad, "")
            if path and Path(path).exists():
                try:
                    self.sounds[key] = mixer.Sound(path)
                except Exception:
                    pass

    def play(self, set_name: str, pad: str, velocity: int = 100) -> None:
        if not self.enabled:
            return
        key = (set_name, pad)
        sound = self.sounds.get(key)
        if sound is None:
            self.load_set(set_name)
            sound = self.sounds.get(key)
        if sound is not None:
            try:
                sound.set_volume(max(0.05, min(1.0, velocity / 127.0)))
                sound.play()
            except Exception:
                pass


# ======================================================================
# MIDI ENGINE
# ======================================================================
class MidiEngine:
    def __init__(self, on_hit):
        self.on_hit = on_hit
        self.port = None
        self.thread = None
        self.stop_event = threading.Event()

    def open(self, device_name: str) -> Tuple[bool, str]:
        self.close()
        if mido is None:
            return False, "mido is not installed. Run: pip install mido python-rtmidi"
        if not device_name:
            return False, "No MIDI input device selected."
        try:
            self.port = mido.open_input(device_name, callback=self._callback)
            return True, device_name
        except Exception as e:
            return False, str(e)

    def _callback(self, msg):
        if getattr(msg, "type", "") == "note_on" and getattr(msg, "velocity", 0) > 0:
            pad = find_pad_for_note(msg.note)
            if pad:
                self.on_hit(pad, int(msg.velocity), time.monotonic(), int(msg.note))

    def close(self):
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None


# ======================================================================
# RECORDING
# ======================================================================
@dataclass
class Hit:
    pad: str
    velocity: int
    time_s: float


def render_hits_to_wav(hits: List[Hit], set_name: str, output: Path) -> None:
    """Mix selected WAV samples into a single mono WAV."""
    rate = 44100
    clips = []

    for hit in hits:
        path = CONFIG["sets"].get(set_name, {}).get(hit.pad, "")
        if not path or not Path(path).exists():
            continue
        try:
            with wave.open(path, "rb") as wf:
                if wf.getsampwidth() != 2:
                    continue
                channels = wf.getnchannels()
                src_rate = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
                values = struct.unpack("<" + "h" * (len(raw) // 2), raw)
                if channels > 1:
                    values = values[::channels]
                # Simple nearest-neighbor resample if necessary.
                if src_rate != rate:
                    new_len = int(len(values) * rate / src_rate)
                    resampled = []
                    for i in range(new_len):
                        resampled.append(values[min(len(values)-1, int(i * src_rate / rate))])
                    values = resampled
                clips.append((hit.time_s, hit.velocity / 127.0, values))
        except Exception:
            continue

    if not clips:
        # Still create a valid WAV.
        with wave.open(str(output), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
            wf.writeframes(b"\x00\x00" * rate)
        return

    length = max(int(t * rate) + len(v) for t, _, v in clips) + 1
    mix = [0.0] * length

    for t, velocity, values in clips:
        start = max(0, int(t * rate))
        gain = min(1.0, 0.25 + velocity * 0.75)
        for i, v in enumerate(values):
            idx = start + i
            if idx < length:
                mix[idx] += (v / 32768.0) * gain

    peak = max(1.0, max(abs(x) for x in mix))
    scale = 0.96 / peak

    with wave.open(str(output), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
        out = bytearray()
        for x in mix:
            out.extend(struct.pack("<h", int(max(-1, min(1, x * scale)) * 32767)))
        wf.writeframes(out)


# ======================================================================
# MAIN APPLICATION
# ======================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()

        icon_path = Path(__file__).resolve().parent / "drumlogo.png"

        if icon_path.exists():
            try:
                image = Image.open(icon_path).convert("RGBA")
                image = image.resize((256, 256), Image.Resampling.LANCZOS)

                self.app_icon = ImageTk.PhotoImage(image)
                self.iconphoto(True, self.app_icon)

            except Exception as e:
                print(f"Could not load application icon: {e}")


        self.title("Most Basic E-Drum Kit Studio Bonanza")
        self.geometry("900x620")
        self.minsize(800, 560)

        self.player = SamplePlayer()
        self.camera_manager = CameraManager()
        self.midi = MidiEngine(self.handle_hit)
        self.active_set = CONFIG.get("default_set", "Default")
        if self.active_set not in CONFIG["sets"]:
            self.active_set = "Default"

        self.active_pad = None
        self.active_until = 0.0
        self.recording = False
        self.paused = False
        self.record_start = 0.0
        self.hits: List[Hit] = []
        self.record_set = self.active_set
        self.camera_thread = None
        self.camera_stop = threading.Event()
        self.camera_path: Optional[Path] = None

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.build_main()

        self.available_midi_devices = []
        self.available_cameras = []

        self.connection_loader = ConnectionLoader(self)

        self.loading_banner = ttk.Frame(self, padding=12)
        self.loading_banner.pack(fill="x")

        self.loading_text = tk.StringVar(
            value="Starting E-Drum Studio..."
        )

        ttk.Label(
            self.loading_banner,
            textvariable=self.loading_text
        ).pack(side="left", padx=10)

        self.loading_progress = ttk.Progressbar(
            self.loading_banner,
            mode="indeterminate",
            length=180
        )
        self.loading_progress.pack(side="right", padx=10)

        self.loading_progress.start(12)

        self.connection_loader.start()
        self.after(100, self.check_connection_results)

    def check_connection_results(self):
        try:
            while True:
                event, value = self.connection_loader.results.get_nowait()

                if event == "status":
                    self.loading_text.set(value)

                elif event == "midi":
                    self.available_midi_devices = value

                elif event == "cameras":
                    self.available_cameras = value
                    self.camera_manager.cached_cameras = value

                elif event == "done":
                    self.loading_progress.stop()
                    self.loading_text.set(
                        f"Ready — "
                        f"{len(self.available_midi_devices)} MIDI device(s), "
                        f"{len(self.available_cameras)} camera(s)"
                    )

                    self.after(
                        2500,
                        self.hide_loading_banner
                    )

                elif event == "error":
                    self.loading_progress.stop()
                    self.loading_text.set(
                        f"Connection detection error: {value}"
                    )

        except queue.Empty:
            pass

        if self.winfo_exists():
            self.after(100, self.check_connection_results)

    def hide_loading_banner(self):
        if self.loading_banner.winfo_exists():
            self.loading_banner.pack_forget()

    def build_main(self):
        outer = ttk.Frame(self, padding=30)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer, text="Most Basic E-Drum Kit Studio Bonanza",
            font=("TkDefaultFont", 28, "bold")
        ).pack(pady=(20, 8))
        ttk.Label(
            outer,
            text="MIDI drum testing, sample-set management, and live recording",
            font=("TkDefaultFont", 11)
        ).pack(pady=(0, 30))

        btns = ttk.Frame(outer)
        btns.pack(expand=True)

        buttons = [
            ("Test", self.open_test),
            ("Record", self.open_record),
            ("Options", self.open_options),
            ("Recordings", self.open_recordings),
            ("About", self.open_about),
        ]
        for i, (label, command) in enumerate(buttons):
            ttk.Button(
                btns, text=label, command=command,
                width=24
            ).grid(row=i, column=0, padx=20, pady=9, ipady=9)

        midi_state = "mido not installed"
        if mido is not None:
            midi_state = "Detecting MIDI devices and cameras in background..."


        ttk.Label(outer, text=midi_state).pack(pady=20)

    # ------------------------------------------------------------------
    # Common visualizer
    # ------------------------------------------------------------------
    def create_visualizer(self, parent):
        canvas = tk.Canvas(parent, height=330, bg="#15171a", highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=12, pady=12)
        # Original drum drawing dimensions.
        DESIGN_WIDTH = 800
        DESIGN_HEIGHT = 500

        resize_state = {
            "width": DESIGN_WIDTH,
            "height": DESIGN_HEIGHT
        }

        def resize_drum_canvas(event):
            old_width = resize_state["width"]
            old_height = resize_state["height"]

            new_width = max(1, event.width)
            new_height = max(1, event.height)

            scale_x = new_width / old_width
            scale_y = new_height / old_height

            canvas.scale(
                "all",
                0,
                0,
                scale_x,
                scale_y
            )

            resize_state["width"] = new_width
            resize_state["height"] = new_height

        canvas.bind("<Configure>", resize_drum_canvas)

        shapes = {}
        coords = {
            "Crash": (125, 80, 215, 125),
            "Ride": (650, 75, 760, 125),
            "China": (470, 60, 560, 100),
            "Hi-Hat": (255, 145, 335, 180),
            "Open Hi-Hat": (255, 145, 335, 180),
            "Tom 1": (375, 125, 440, 170),
            "Tom 2": (465, 120, 535, 170),
            "Tom 3": (555, 130, 625, 175),
            "Snare": (330, 195, 410, 235),
            "Kick": (445, 220, 545, 305),
        }
        labels = {}
        for pad, (x1, y1, x2, y2) in coords.items():
            if pad not in CONFIG["pads"]:
                continue
            oval = canvas.create_oval(
                x1, y1, x2, y2, fill="#3b4148",
                outline="#9aa4ad", width=2
            )
            shapes[pad] = oval
            label = canvas.create_text(
                (x1+x2)//2, (y1+y2)//2,
                text=CONFIG["pads"][pad]["name"],
                fill="white", font=("TkDefaultFont", 10, "bold")
            )
            labels[pad] = label

        canvas.create_text(
            20, 310, anchor="w",
            text="MIDI activity",
            fill="#aeb7c0"
        )
        return canvas, shapes, labels

    def flash_pad(self, canvas, shapes, pad):
        if pad not in shapes:
            return
        canvas.itemconfigure(shapes[pad], fill="#f5c542")
        self.after(90, lambda: canvas.itemconfigure(
            shapes[pad], fill="#3b4148"
        ) if canvas.winfo_exists() else None)

    # ------------------------------------------------------------------
    # Test window
    # ------------------------------------------------------------------
    def open_test(self):
        win = tk.Toplevel(self)
        win.title("Test E-Drum Kit")
        win.geometry("900x560")

        top = ttk.Frame(win, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="MIDI device:").pack(side="left")
        devices = self.available_midi_devices or []
        device_var = tk.StringVar(
            value=CONFIG.get("default_midi_device", "")
            if CONFIG.get("default_midi_device", "") in devices
            else (devices[0] if devices else "")
        )
        device_box = ttk.Combobox(
            top, textvariable=device_var, values=devices,
            state="readonly", width=48
        )
        device_box.pack(side="left", padx=8)

        ttk.Label(top, text="Drum set:").pack(side="left", padx=(12, 0))
        set_var = tk.StringVar(value=self.active_set)
        set_box = ttk.Combobox(
            top, textvariable=set_var,
            values=list(CONFIG["sets"].keys()),
            state="readonly", width=22
        )
        set_box.pack(side="left", padx=8)

        status = tk.StringVar(value="Select a MIDI device and click Connect.")
        ttk.Label(win, textvariable=status).pack(anchor="w", padx=14)

        # Resizable split layout: drum kit | camera
        split = ttk.PanedWindow(win, orient=tk.HORIZONTAL)
        split.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10
        )

        # Left side: drum kit
        drum_frame = ttk.Frame(split)
        split.add(drum_frame, weight=1)

        canvas, shapes, labels = self.create_visualizer(drum_frame)

        # Right pane exists even when the camera is disabled.
        camera_frame = ttk.Frame(split)
        split.add(camera_frame, weight=1)

        camera_enabled = tk.BooleanVar(value=False)
        ttk.Label(camera_frame, text="Camera disabled by default").pack(
            anchor="w", padx=10, pady=(4, 0)
        )

        camera_preview = CameraPreview(camera_frame, self.camera_manager)
        camera_preview.pack(fill="both", expand=True)

        def toggle_test_camera():
            if camera_enabled.get():
                camera_preview.start()
            else:
                camera_preview.stop()

        ttk.Checkbutton(
            camera_frame,
            text="Enable camera",
            variable=camera_enabled,
            command=toggle_test_camera,
        ).pack(anchor="w", padx=10, pady=5, before=camera_preview)

        def connect():
            ok, msg = self.midi.open(device_var.get())
            if ok:
                self.active_set = set_var.get()
                self.player.load_set(self.active_set)
                status.set(f"Connected: {msg}")
            else:
                status.set(msg)

        def refresh():
            vals = list_midi_inputs()
            device_box["values"] = vals
            if vals and not device_var.get():
                device_var.set(vals[0])

        bottom = ttk.Frame(win, padding=10)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Connect / Reconnect", command=connect).pack(side="left")
        ttk.Button(bottom, text="Refresh MIDI Devices", command=refresh).pack(side="left", padx=8)
        ttk.Button(bottom, text="Close", command=lambda: close()).pack(side="right")

        # Save references so the MIDI callback can update this window.
        original = self.handle_hit

        def local_hit(pad, velocity, when, note):
            original(pad, velocity, when, note)
            if win.winfo_exists():
                self.flash_pad(canvas, shapes, pad)
                status.set(
                    f"Hit: {CONFIG['pads'][pad]['name']}  | "
                    f"MIDI note: {note}  | velocity: {velocity}"
                )

        # Temporarily route hits to this visualizer.
        old_callback = self.midi.on_hit
        self.midi.on_hit = local_hit

        def close():
            self.midi.close()
            self.midi.on_hit = old_callback
            camera_preview.stop()
            win.destroy()

        def set_initial_split():
            if split.winfo_exists():
                width = split.winfo_width()

                if width > 100:
                    split.sashpos(0, width // 2)

        win.after(150, set_initial_split)

        win.protocol("WM_DELETE_WINDOW", close)

    # ------------------
    # About
    #-------------------
    def open_about(self):
        win = tk.Toplevel(self)
        win.title("About")
        win.geometry("500x350")
        win.resizable(False, False)

        # Main content
        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)

        # Placeholder text — replace later
        ttk.Label(
            frame,
            text="About Most Basic E-Drum Studio Bonanza\n\nCreated for fun by a dude who recently "
                 " bought e-drum kit. Idea is to handle basic MIDI stuff (adding own sounds, recording"
                 " yourself while playing and (soon) more, without the hassle of downloading number"
                 " of overpacked software. Do you want to add something? Is something not working"
                 " as it should? Let me know: astorbeon@protonmail.com",
            font=("TkDefaultFont", 11),
            justify="center",
            anchor="center"
        ).pack(fill="both", expand=True)

        # Close button
        ttk.Button(
            frame,
            text="Close",
            command=win.destroy
        ).pack(pady=(10, 0))
    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    def open_options(self):
        win = tk.Toplevel(self)
        win.title("Options")
        win.geometry("980x650")

        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        general = ttk.Frame(notebook, padding=15)
        sets_tab = ttk.Frame(notebook, padding=15)
        pads_tab = ttk.Frame(notebook, padding=15)
        notebook.add(general, text="General")
        notebook.add(sets_tab, text="Sound Sets")
        notebook.add(pads_tab, text="Pads")

        # General
        ttk.Label(general, text="Default MIDI input:").grid(row=0, column=0, sticky="w", pady=8)
        midi_var = tk.StringVar(value=CONFIG.get("default_midi_device", ""))
        midi_box = ttk.Combobox(
            general, textvariable=midi_var,
            values=list_midi_inputs(), state="readonly", width=55
        )
        midi_box.grid(row=0, column=1, sticky="ew", padx=10)

        ttk.Label(general, text="Default sound set:").grid(row=1, column=0, sticky="w", pady=8)
        set_var = tk.StringVar(value=CONFIG.get("default_set", "Default"))
        set_box = ttk.Combobox(
            general, textvariable=set_var,
            values=list(CONFIG["sets"].keys()), state="readonly", width=30
        )
        set_box.grid(row=1, column=1, sticky="w", padx=10)

        ttk.Label(general, text="Default camera:").grid(
            row=2, column=0, sticky="w", pady=8
        )

        camera_var = tk.StringVar()

        camera_list = list_cameras()

        camera_values = [
            f"{index}: {name}"
            for index, name in camera_list
        ]

        camera_box = ttk.Combobox(
            general,
            textvariable=camera_var,
            values=camera_values,
            state="readonly",
            width=45
        )
        camera_box.grid(row=2, column=1, sticky="w", padx=10)

        # Select the currently saved camera.
        saved_camera = int(CONFIG.get("camera_index", 0))

        for value in camera_values:
            if value.startswith(f"{saved_camera}:"):
                camera_var.set(value)
                break

        if not camera_var.get() and camera_values:
            camera_var.current(0)
        ttk.Label(
            general,
            text="Settings are saved directly into this Python script when you press Save.",
            wraplength=650
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=30)

        # Sound sets
        left = ttk.Frame(sets_tab)
        left.pack(side="left", fill="y", padx=(0, 15))
        right = ttk.Frame(sets_tab)
        right.pack(side="left", fill="both", expand=True)

        set_list = tk.Listbox(left, width=25, height=20)
        set_list.pack(fill="y")
        for name in CONFIG["sets"]:
            set_list.insert("end", name)

        selected_set = tk.StringVar(value=self.active_set)

        ttk.Label(right, text="Set name:").grid(row=0, column=0, sticky="w")
        set_name_var = tk.StringVar(value=self.active_set)
        ttk.Entry(right, textvariable=set_name_var, width=40).grid(row=0, column=1, sticky="w", padx=8)

        file_vars = {}
        rows = {}

        def refresh_set_editor(*_):
            for w in right.grid_slaves():
                info = w.grid_info()
                if int(info["row"]) >= 1:
                    w.destroy()
            name = set_name_var.get()
            if name not in CONFIG["sets"]:
                return
            for r, pad in enumerate(PAD_ORDER, start=1):
                ttk.Label(right, text=CONFIG["pads"][pad]["name"]).grid(row=r, column=0, sticky="w", pady=4)
                var = tk.StringVar(value=CONFIG["sets"][name].get(pad, ""))
                file_vars[pad] = var
                ttk.Entry(right, textvariable=var, width=58).grid(row=r, column=1, sticky="ew", padx=8)

                def browse(p=pad, v=var):
                    f = filedialog.askopenfilename(
                        title=f"Choose WAV for {CONFIG['pads'][p]['name']}",
                        filetypes=[("WAV files", "*.wav"), ("All files", "*.*")]
                    )
                    if f:
                        v.set(f)

                ttk.Button(right, text="Browse", command=browse).grid(row=r, column=2, padx=4)

        def new_set():
            name = "New Set"
            n = 2
            while name in CONFIG["sets"]:
                name = f"New Set {n}"; n += 1
            CONFIG["sets"][name] = {}
            set_list.insert("end", name)
            set_name_var.set(name)
            refresh_set_editor()

        def delete_set():
            sel = set_list.curselection()
            if not sel:
                return
            name = set_list.get(sel[0])
            if name == "Default":
                messagebox.showwarning("Cannot delete", "The Default set cannot be deleted.")
                return
            if messagebox.askyesno("Delete set", f"Delete '{name}'?"):
                del CONFIG["sets"][name]
                set_list.delete(sel[0])
                set_name_var.set("Default")
                refresh_set_editor()

        def save_set():
            old_name = selected_set.get()
            new_name = set_name_var.get().strip()
            if not new_name:
                messagebox.showerror("Invalid name", "Enter a sound-set name.")
                return
            if old_name != new_name and old_name in CONFIG["sets"]:
                CONFIG["sets"][new_name] = CONFIG["sets"].pop(old_name)
            CONFIG["sets"].setdefault(new_name, {})
            for pad, var in file_vars.items():
                CONFIG["sets"][new_name][pad] = var.get().strip()
            selected_set.set(new_name)
            set_list.delete(0, "end")
            for name in CONFIG["sets"]:
                set_list.insert("end", name)
            set_name_var.set(new_name)
            messagebox.showinfo("Saved", "Sound set updated. Press the main Save button to persist it.")

        set_list.bind("<<ListboxSelect>>", lambda e: (
            set_name_var.set(set_list.get(set_list.curselection()[0]))
            if set_list.curselection() else None,
            selected_set.set(set_list.get(set_list.curselection()[0]))
            if set_list.curselection() else None,
            refresh_set_editor()
        ))

        ttk.Button(left, text="New Set", command=new_set).pack(pady=(10, 4), fill="x")
        ttk.Button(left, text="Delete Set", command=delete_set).pack(fill="x")
        ttk.Button(right, text="Apply Set Changes", command=save_set).grid(
            row=12, column=0, columnspan=3, pady=14
        )
        refresh_set_editor()

        # Pads
        pad_tree = ttk.Treeview(
            pads_tab, columns=("pad", "note", "name"), show="headings", height=18
        )
        for col, text in [("pad", "Pad"), ("note", "MIDI Note"), ("name", "Display Name")]:
            pad_tree.heading(col, text=text)
        pad_tree.column("pad", width=180)
        pad_tree.column("note", width=120)
        pad_tree.column("name", width=300)
        pad_tree.pack(fill="both", expand=True)

        pad_entries = {}
        for pad in PAD_ORDER:
            data = CONFIG["pads"].get(pad, {})
            item = pad_tree.insert("", "end", values=(pad, data.get("note", ""), data.get("name", pad)))
            pad_entries[item] = pad

        editor = ttk.Frame(pads_tab)
        editor.pack(fill="x", pady=12)
        ttk.Label(editor, text="MIDI note:").grid(row=0, column=0, padx=5)
        note_var = tk.StringVar()
        ttk.Entry(editor, textvariable=note_var, width=10).grid(row=0, column=1)
        ttk.Label(editor, text="Display name:").grid(row=0, column=2, padx=5)
        name_var = tk.StringVar()
        ttk.Entry(editor, textvariable=name_var, width=30).grid(row=0, column=3)

        def load_pad_edit(_=None):
            sel = pad_tree.selection()
            if not sel:
                return
            vals = pad_tree.item(sel[0], "values")
            note_var.set(vals[1])
            name_var.set(vals[2])

        def apply_pad_edit():
            sel = pad_tree.selection()
            if not sel:
                return
            pad = pad_entries[sel[0]]
            try:
                note = int(note_var.get())
                if not 0 <= note <= 127:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid MIDI note", "MIDI note must be an integer from 0 to 127.")
                return
            CONFIG["pads"][pad]["note"] = note
            CONFIG["pads"][pad]["name"] = name_var.get().strip() or pad
            pad_tree.item(sel[0], values=(pad, note, CONFIG["pads"][pad]["name"]))

        pad_tree.bind("<<TreeviewSelect>>", load_pad_edit)
        ttk.Button(editor, text="Apply Pad Change", command=apply_pad_edit).grid(row=0, column=4, padx=10)

        def save_all():
            try:
                if camera_var.get():
                    cam = int(camera_var.get().split(":", 1)[0])
                else:
                    cam = 0
            except (ValueError, IndexError):
                messagebox.showerror(
                    "Camera",
                    "Please select a valid camera."
                )
                return

            # Apply current sound-set editor values first.
            save_set()
            CONFIG["default_midi_device"] = midi_var.get()
            CONFIG["default_set"] = set_var.get() if set_var.get() in CONFIG["sets"] else "Default"
            CONFIG["camera_index"] = cam
            try:
                save_config_to_this_script()
                messagebox.showinfo(
                    "Settings saved",
                    "Settings were saved inside the Python script. They will be restored after restart."
                )
            except Exception as e:
                messagebox.showerror(
                    "Save failed",
                    f"Could not rewrite the script:\n{e}\n\n"
                    "Make sure the .py file is writable."
                )

        ttk.Button(win, text="Save Settings", command=save_all).pack(side="left", padx=15, pady=10)
        ttk.Button(win, text="Close", command=win.destroy).pack(side="right", padx=15, pady=10)

    # ------------------------------------------------------------------
    # Record
    # ------------------------------------------------------------------
    def open_record(self):
        win = tk.Toplevel(self)
        win.title("Record E-Drum Performance")
        win.geometry("950x650")

        top = ttk.Frame(win, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="MIDI device:").pack(side="left")
        devices = list_midi_inputs()
        dev_var = tk.StringVar(value=CONFIG.get("default_midi_device", "") if CONFIG.get("default_midi_device", "") in devices else (devices[0] if devices else ""))
        dev_box = ttk.Combobox(top, textvariable=dev_var, values=devices, state="readonly", width=40)
        dev_box.pack(side="left", padx=8)

        ttk.Label(top, text="Sound set:").pack(side="left", padx=(10, 0))
        set_var = tk.StringVar(value=self.active_set)
        set_box = ttk.Combobox(top, textvariable=set_var, values=list(CONFIG["sets"]), state="readonly", width=20)
        set_box.pack(side="left", padx=8)

        cam_var = tk.BooleanVar(value=False)

        def toggle_camera():
            if cam_var.get():
                camera_preview.pack(
                    side="right",
                    fill="both",
                    expand=True,
                    padx=10,
                    pady=10
                )
                camera_preview.start()
            else:
                camera_preview.stop()
                camera_preview.pack_forget()

        ttk.Checkbutton(
            top,
            text="Enable camera",
            variable=cam_var,
            command=toggle_camera
        ).pack(side="left", padx=10)
        status = tk.StringVar(value="Ready.")
        ttk.Label(win, textvariable=status).pack(anchor="w", padx=15)

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True)

        drum_frame = ttk.Frame(body)
        drum_frame.pack(
            side="left",
            fill="both",
            expand=True
        )

        canvas, shapes, labels = self.create_visualizer(
            drum_frame
        )

        camera_preview = CameraPreview(
            body,
            self.camera_manager
        )


        controls = ttk.Frame(win, padding=10)
        controls.pack(fill="x")

        def connect():
            ok, msg = self.midi.open(dev_var.get())
            if ok:
                self.active_set = set_var.get()
                self.player.load_set(self.active_set)
                status.set(f"Connected: {msg}")
            else:
                status.set(msg)

        def start_record():
            if self.recording:
                return
            if not self.midi.port:
                connect()
            if not self.midi.port:
                status.set("Connect to a MIDI device first.")
                return
            self.record_set = set_var.get()
            self.hits = []
            self.recording = True
            self.paused = False
            self.record_start = time.monotonic()
            status.set("● Recording...")

            if cam_var.get():
                temp = RECORDINGS_DIR / f"_camera_{int(time.time())}.mp4"

                if self.camera_manager.start_recording(temp):
                    self.camera_path = temp
                else:
                    self.camera_path = None
                    messagebox.showwarning(
                        "Camera",
                        "Could not start camera recording."
                    )

        def stop_record():
            self.recording = False
            self.paused = False
            self.stop_camera_recording()
            status.set(f"Stopped. {len(self.hits)} hit(s) captured.")

        def pause_record():
            if not self.recording:
                return
            self.paused = not self.paused
            status.set("Paused." if self.paused else "● Recording...")

        def save_record():
            if not self.hits:
                messagebox.showwarning("Nothing to save", "Play something before saving.")
                return
            self.recording = False
            self.paused = False
            self.stop_camera_recording()

            stamp = time.strftime("%Y%m%d_%H%M%S")
            folder = RECORDINGS_DIR / f"recording_{stamp}"
            folder.mkdir(parents=True, exist_ok=True)

            events = [
                {"pad": h.pad, "velocity": h.velocity, "time": h.time_s}
                for h in self.hits
            ]
            (folder / "performance.json").write_text(
                json.dumps({
                    "sound_set": self.record_set,
                    "created": stamp,
                    "events": events
                }, indent=2),
                encoding="utf-8"
            )

            render_hits_to_wav(self.hits, self.record_set, folder / "performance.wav")

            if self.camera_path and self.camera_path.exists():
                shutil.copy2(self.camera_path, folder / "camera.mp4")

            self.camera_path = None
            status.set(f"Saved to {folder}")
            messagebox.showinfo("Recording saved", f"Saved recording to:\n{folder}")

        ttk.Button(controls, text="Connect", command=connect).pack(side="left", padx=4)
        ttk.Button(controls, text="● Record", command=start_record).pack(side="left", padx=4)
        ttk.Button(controls, text="Pause / Resume", command=pause_record).pack(side="left", padx=4)
        ttk.Button(controls, text="Stop", command=stop_record).pack(side="left", padx=4)
        ttk.Button(controls, text="Save Recording", command=save_record).pack(side="left", padx=4)
        ttk.Button(controls, text="Close", command=lambda: self.close_record_window(win)).pack(side="right", padx=4)

        win.protocol("WM_DELETE_WINDOW", lambda: self.close_record_window(win))

    def close_record_window(self, win):
        self.recording = False
        self.paused = False
        self.stop_camera_recording()
        self.camera_manager.close()
        self.midi.close()
        win.destroy()

    def handle_hit(self, pad, velocity, when, note):
        self.player.play(self.active_set, pad, velocity)
        if self.recording and not self.paused:
            self.hits.append(Hit(
                pad=pad,
                velocity=velocity,
                time_s=max(0.0, when - self.record_start)
            ))

    def start_camera_recording(self):
        if cv2 is None:
            messagebox.showwarning(
                "Camera unavailable",
                "OpenCV is not installed. Run: pip install opencv-python"
            )
            return
        self.camera_stop.clear()
        temp = RECORDINGS_DIR / f"_camera_{int(time.time())}.mp4"
        self.camera_path = temp
        self.camera_thread = threading.Thread(
            target=self._camera_worker, args=(temp,), daemon=True
        )
        self.camera_thread.start()

    def _camera_worker(self, path: Path):
        cap = cv2.VideoCapture(int(CONFIG.get("camera_index", 0)))
        if not cap.isOpened():
            return
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps < 1:
            fps = 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
        while not self.camera_stop.is_set():
            ok, frame = cap.read()
            if not ok:
                break
            writer.write(frame)
            time.sleep(max(0, 1.0 / fps))
        writer.release()
        cap.release()

    def stop_camera_recording(self):
        self.camera_manager.stop_recording()
    # ------------------------------------------------------------------
    # Recordings
    # ------------------------------------------------------------------
    def open_recordings(self):
        win = tk.Toplevel(self)
        win.title("Recordings")
        win.geometry("760x480")

        ttk.Label(
            win, text=f"Saved recordings: {RECORDINGS_DIR}",
            padding=10
        ).pack(anchor="w")

        tree = ttk.Treeview(
            win, columns=("name", "set", "events"),
            show="headings"
        )
        for c, title in [("name", "Recording"), ("set", "Sound Set"), ("events", "Hits")]:
            tree.heading(c, text=title)
        tree.column("name", width=300)
        tree.column("set", width=180)
        tree.column("events", width=80)
        tree.pack(fill="both", expand=True, padx=10, pady=10)

        def refresh():
            tree.delete(*tree.get_children())
            for folder in sorted(RECORDINGS_DIR.glob("recording_*"), reverse=True):
                info_file = folder / "performance.json"
                if not info_file.exists():
                    continue
                try:
                    info = json.loads(info_file.read_text(encoding="utf-8"))
                    tree.insert("", "end", iid=str(folder), values=(
                        folder.name,
                        info.get("sound_set", ""),
                        len(info.get("events", []))
                    ))
                except Exception:
                    pass

        def play_audio():
            sel = tree.selection()
            if not sel:
                return
            wav = Path(sel[0]) / "performance.wav"
            if not wav.exists():
                return
            if mixer is None:
                messagebox.showwarning("Audio", "pygame is not installed.")
                return
            try:
                mixer.music.load(str(wav))
                mixer.music.play()
            except Exception as e:
                messagebox.showerror("Playback", str(e))

        def open_folder():
            sel = tree.selection()
            if not sel:
                return
            folder = Path(sel[0])
            try:
                if sys.platform.startswith("win"):
                    os.startfile(folder)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(folder)])
                else:
                    subprocess.Popen(["xdg-open", str(folder)])
            except Exception:
                pass

        bottom = ttk.Frame(win, padding=10)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Refresh", command=refresh).pack(side="left")
        ttk.Button(bottom, text="Play Audio", command=play_audio).pack(side="left", padx=8)
        ttk.Button(bottom, text="Open Folder", command=open_folder).pack(side="left")
        ttk.Button(bottom, text="Close", command=win.destroy).pack(side="right")

        refresh()

    def on_close(self):
        self.recording = False
        self.stop_camera_recording()
        self.midi.close()
        self.camera_manager.close()
        if mixer is not None:
            try:
                mixer.quit()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
