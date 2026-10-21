"""Portable settings, sample rendering, and recording timeline."""

from __future__ import annotations
import copy
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave

import numpy as np

DEFAULT_PADS = [
    ("Kick", 36),
    ("Snare", 38),
    ("Hi-Hat", 42),
    ("Open Hi-Hat", 46),
    ("Tom 1", 48),
    ("Tom 2", 45),
    ("Tom 3", 43),
    ("Crash", 49),
    ("Ride", 51),
    ("China", 52),
]


def resources():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def app_data():
    base = Path(
        os.environ.get(
            "APPDATA",
            Path.home()
            / (
                "Library/Application Support"
                if sys.platform == "darwin"
                else ".local/share"
            ),
        )
    )
    return base / "EDrumBonanza"


class Settings:
    def __init__(self, root=None):
        self.root = Path(root or app_data())
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "settings.json"
        pads = [
            dict(
                id=uuid.uuid4().hex,
                name=n,
                note=k,
                channel=0,
                device="*",
                gain=1.0,
                sensitivity=1.0,
            )
            for n, k in DEFAULT_PADS
        ]
        self.data = dict(
            version=1,
            preset="Default",
            sound_set="Default",
            presets={"Default": pads},
            sets={
                "Default": {
                    p["id"]: str(
                        resources()
                        / "default_sounds"
                        / (p["name"].lower().replace(" ", "_") + ".wav")
                    )
                    for p in pads
                }
            },
        )
        if self.path.exists():
            try:
                candidate = json.loads(self.path.read_text())
                self.validate(candidate)
                self.data = candidate
            except (ValueError, KeyError, TypeError) as exc:
                backup = self.path.with_name(f"settings.corrupt-{time.time_ns()}.json")
                shutil.copy2(self.path, backup)
                raise ValueError(
                    f"Invalid settings; backup saved to {backup}: {exc}"
                ) from exc
        # A one-file executable gets a fresh extraction directory on every run.
        # Resolve bundled defaults by stable filename, independent of pad renames.
        for container, key in self.sample_entries(self.data):
            current = container[key]
            old_path = Path(current)
            bundled = resources() / "default_sounds" / old_path.name
            if (
                current
                and "default_sounds" in old_path.parts
                and not old_path.is_file()
                and bundled.is_file()
            ):
                container[key] = str(bundled)

    @staticmethod
    def validate(data):
        if data.get("version") != 1 or not data.get("presets") or not data.get("sets"):
            raise ValueError("Unsupported or empty configuration")
        if (
            data["preset"] not in data["presets"]
            or data["sound_set"] not in data["sets"]
        ):
            raise ValueError("Selected preset or sound set is missing")
        for pads in data["presets"].values():
            ids = set()
            for p in pads:
                if (
                    not p["name"]
                    or p["id"] in ids
                    or not 0 <= int(p["note"]) <= 127
                    or not 0 <= int(p["channel"]) <= 16
                ):
                    raise ValueError("Invalid pad mapping")
                if (
                    not 0 <= float(p["gain"]) <= 4
                    or not 0.1 <= float(p["sensitivity"]) <= 4
                ):
                    raise ValueError("Invalid gain or sensitivity")
                ids.add(p["id"])
        for sound_set in data["sets"].values():
            if not isinstance(sound_set, dict) or any(
                not isinstance(v, str) for v in sound_set.values()
            ):
                raise ValueError("Invalid sound set")

        for layers in data.get("layers", {}).values():
            for entries in layers.values():
                limits = [entry["max_velocity"] for entry in entries]
                if (
                    not limits
                    or limits != sorted(set(limits))
                    or limits[-1] != 127
                    or limits[0] < 1
                ):
                    raise ValueError(
                        "Velocity layers need unique ascending thresholds ending at 127"
                    )
                if any(not isinstance(entry["sample"], str) for entry in entries):
                    raise ValueError("Invalid layer sample")

    def sample(self, pad_id, velocity=127):
        layers = (
            self.data.get("layers", {}).get(self.data["sound_set"], {}).get(pad_id, [])
        )
        for layer in layers:
            if velocity <= layer["max_velocity"]:
                return layer["sample"]
        return self.data["sets"][self.data["sound_set"]].get(pad_id, "")

    @staticmethod
    def sample_entries(data):
        if data.get("backing_path"):
            yield data, "backing_path"
        for sounds in data["sets"].values():
            for key in sounds:
                yield sounds, key
        for pads in data.get("layers", {}).values():
            for layers in pads.values():
                for layer in layers:
                    yield layer, "sample"

    @property
    def pads(self):
        return self.data["presets"][self.data["preset"]]

    def save(self):
        self.validate(self.data)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2))
        temporary.replace(self.path)

    def import_sample(self, source):
        read_sample(source)  # Reject unreadable or unsupported files before copying.
        folder = self.root / "samples" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        target = folder / Path(source).name
        shutil.copy2(source, target)
        return str(target)

    def export_bundle(self, target):
        import zipfile

        data = copy.deepcopy(self.data)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for container, key in self.sample_entries(data):
                path = Path(container[key])
                if path.is_file():
                    name = "samples/" + uuid.uuid4().hex + "/" + path.name
                    archive.write(path, name)
                    container[key] = name
                else:
                    container[key] = ""
            archive.writestr("settings.json", json.dumps(data))

    def import_bundle(self, source):
        import zipfile

        with zipfile.ZipFile(source) as archive:
            if sum(i.file_size for i in archive.infolist()) > 512 * 1024 * 1024:
                raise ValueError("Preset archive exceeds 512 MB")
            data = json.loads(archive.read("settings.json"))
            self.validate(data)
            for container, key in self.sample_entries(data):
                name = container[key]
                if not name:
                    continue
                if not name.startswith("samples/") or ".." in Path(name).parts:
                    raise ValueError("Invalid sample path in archive")
                with tempfile.TemporaryDirectory() as temp:
                    sample = Path(temp) / Path(name).name
                    sample.write_bytes(archive.read(name))
                    container[key] = self.import_sample(sample)
            self.data = data
            self.save()


def read_sample(path, rate=44100):
    with wave.open(str(path), "rb") as source:
        width, channels, hz = (
            source.getsampwidth(),
            source.getnchannels(),
            source.getframerate(),
        )
        raw = source.readframes(source.getnframes())
    if width not in (1, 2, 3, 4) or channels not in (1, 2):
        raise ValueError("Use mono or stereo PCM WAV samples (8–32 bit)")
    if width == 1:
        data = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif width == 3:
        bytes_ = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        numbers = bytes_[:, 0] | bytes_[:, 1] << 8 | bytes_[:, 2] << 16
        data = ((numbers ^ 0x800000) - 0x800000).astype(np.float32) / 8388608
    else:
        data = np.frombuffer(raw, "<i2" if width == 2 else "<i4").astype(np.float32) / (
            2 ** (width * 8 - 1)
        )
    data = data.reshape(-1, channels)
    if channels == 1:
        data = np.repeat(data, 2, axis=1)
    if hz != rate and len(data):
        positions = np.arange(round(len(data) * rate / hz)) * hz / rate
        data = np.stack(
            [np.interp(positions, np.arange(len(data)), data[:, i]) for i in range(2)],
            axis=1,
        ).astype(np.float32)
    return data


class Timeline:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.state = "idle"
        self.hits = []
        self.chokes = []
        self.backing = ""
        self.backing_gain = 0.5
        self.duration = 0.0

    def start(self):
        self.hits = []
        self.chokes = []
        self.backing = ""
        self.backing_gain = 0.5
        self.duration = 0.0
        self.started = self.clock()
        self.paused_total = 0.0
        self.state = "recording"

    def elapsed(self):
        if self.state == "idle":
            return self.duration
        end = self.paused_at if self.state == "paused" else self.clock()
        return max(0.0, end - self.started - self.paused_total)

    def pause(self):
        if self.state == "recording":
            self.paused_at = self.clock()
            self.state = "paused"
        elif self.state == "paused":
            self.paused_total += self.clock() - self.paused_at
            self.state = "recording"

    def stop(self):
        self.duration = self.elapsed()
        self.state = "idle"

    def hit(self, **event):
        if self.state == "recording":
            self.hits.append(dict(time=self.elapsed(), **event))

    def choke(self, pad_id):
        if self.state == "recording":
            self.chokes.append(dict(time=self.elapsed(), pad_id=pad_id))

    def export_events(self, target, bpm=120, beats=4):
        events = []
        for hit in self.hits:
            beat = hit["time"] * bpm / 60
            events.append(
                dict(**hit, bar=int(beat // beats) + 1, beat=beat % beats + 1)
            )
        if str(target).lower().endswith(".csv"):
            with open(target, "w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=[
                        "time",
                        "pad",
                        "pad_id",
                        "device",
                        "channel",
                        "note",
                        "velocity",
                        "sample",
                        "gain",
                        "bar",
                        "beat",
                    ],
                )
                writer.writeheader()
                writer.writerows(events)
        else:
            Path(target).write_text(
                json.dumps(
                    dict(
                        bpm=bpm,
                        beats_per_bar=beats,
                        duration=self.duration,
                        hits=events,
                        chokes=self.chokes,
                    ),
                    indent=2,
                )
            )

    def export_midi(self, target, bpm=120, beats=4):
        import mido

        file = mido.MidiFile()
        track = mido.MidiTrack()
        file.tracks.append(track)
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
        track.append(mido.MetaMessage("time_signature", numerator=beats, denominator=4))
        events = []
        for hit in self.hits:
            tick = round(hit["time"] * bpm / 60 * file.ticks_per_beat)
            channel = max(0, hit["channel"] - 1)
            events.extend(
                [
                    (tick, True, hit, channel),
                    (tick + max(1, file.ticks_per_beat // 16), False, hit, channel),
                ]
            )
        for choke in self.chokes:
            hit = next(
                (
                    h
                    for h in reversed(self.hits)
                    if h["pad_id"] == choke["pad_id"] and h["time"] <= choke["time"]
                ),
                None,
            )
            if hit:
                tick = round(choke["time"] * bpm / 60 * file.ticks_per_beat)
                track_message = dict(hit, choke=True)
                events.append((tick, False, track_message, max(0, hit["channel"] - 1)))
        previous = 0
        for tick, on, hit, channel in sorted(events, key=lambda e: (e[0], e[1])):
            if hit.get("choke"):
                track.append(
                    mido.Message(
                        "polytouch",
                        note=hit["note"],
                        value=127,
                        channel=channel,
                        time=tick - previous,
                    )
                )
            else:
                track.append(
                    mido.Message(
                        "note_on" if on else "note_off",
                        note=hit["note"],
                        velocity=hit["velocity"] if on else 0,
                        channel=channel,
                        time=tick - previous,
                    )
                )
            previous = tick
        file.save(target)

    def render(self, target, rate=44100):
        # Stream chunks so long recordings do not require a full-sized audio buffer.
        cache = {}
        events = []
        end = max(1, round(self.duration * rate))
        if self.backing:
            sample = read_sample(self.backing, rate)[:end]
            events.append((0, sample, self.backing_gain))
        for hit in self.hits:
            filename = hit["sample"]
            if not filename:
                continue
            if filename not in cache:
                cache[filename] = read_sample(filename, rate)
            sample = cache[filename]
            choke = next(
                (
                    c
                    for c in self.chokes
                    if c["pad_id"] == hit["pad_id"] and c["time"] >= hit["time"]
                ),
                None,
            )
            if choke:
                length = max(0, round((choke["time"] - hit["time"]) * rate))
                sample = sample[:length].copy()
                fade = min(len(sample), round(rate * 0.04))
                if fade:
                    sample[-fade:] *= np.linspace(1, 0, fade)[:, None]
            start = round(hit["time"] * rate)
            events.append((start, sample, hit["velocity"] / 127 * hit["gain"]))
            end = max(end, start + len(sample))
        with wave.open(str(target), "wb") as output:
            output.setparams((2, 2, rate, 0, "NONE", "not compressed"))
            for offset in range(0, end, rate):
                chunk = np.zeros((min(rate, end - offset), 2), np.float32)
                for start, sample, gain in events:
                    lo, hi = max(offset, start), min(
                        offset + len(chunk), start + len(sample)
                    )
                    if lo < hi:
                        chunk[lo - offset : hi - offset] += (
                            sample[lo - start : hi - start] * gain
                        )
                output.writeframes(
                    (np.clip(chunk, -1, 1) * 32767).astype("<i2").tobytes()
                )


def ffmpeg():
    import imageio_ffmpeg

    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


def mux_video(video, audio, target):
    result = subprocess.run(
        [
            ffmpeg(),
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
