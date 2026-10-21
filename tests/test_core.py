import json
from pathlib import Path
import subprocess
import wave

import cv2
import mido
import numpy as np
import pytest
from edrum.core import Settings, Timeline, read_sample, mux_video, ffmpeg


def test_pause_and_exports(tmp_path):
    now = [100.0]
    timeline = Timeline(lambda: now[0])
    settings = Settings(tmp_path / "settings")
    pad = settings.pads[0]
    sample = settings.data["sets"]["Default"][pad["id"]]
    timeline.start()
    now[0] += 0.2
    timeline.hit(
        pad="Kick",
        pad_id=pad["id"],
        device="test",
        channel=10,
        note=36,
        velocity=100,
        sample=sample,
        gain=0.8,
    )
    timeline.pause()
    now[0] += 10
    timeline.hit(pad="ignored")
    timeline.pause()
    now[0] += 0.3
    timeline.stop()
    assert timeline.duration == pytest.approx(0.5)
    assert len(timeline.hits) == 1
    timeline.export_events(tmp_path / "events.json")
    timeline.export_events(tmp_path / "events.csv")
    assert json.loads((tmp_path / "events.json").read_text())["hits"][0][
        "time"
    ] == pytest.approx(0.2)
    timeline.export_midi(tmp_path / "take.mid")
    notes = [
        m for m in mido.MidiFile(tmp_path / "take.mid").tracks[0] if m.type == "note_on"
    ]
    assert len(notes) == 1 and notes[0].channel == 9 and notes[0].note == 36
    timeline.render(tmp_path / "take.wav")
    audio = read_sample(tmp_path / "take.wav")
    assert np.max(np.abs(audio[:8000])) == 0
    assert np.max(np.abs(audio[8820:])) > 0.01


def test_portable_settings(tmp_path):
    original = Settings(tmp_path / "a")
    original.pads[0]["name"] = "My kick"
    original.save()
    assert Settings(tmp_path / "a").pads[0]["name"] == "My kick"
    original.export_bundle(tmp_path / "bundle.zip")
    restored = Settings(tmp_path / "b")
    restored.import_bundle(tmp_path / "bundle.zip")
    assert restored.pads[0]["name"] == "My kick"
    for filename in restored.data["sets"]["Default"].values():
        assert Path(filename).is_file()
        assert str(tmp_path / "b") in filename
    restored.save()
    assert Settings(tmp_path / "b").data == restored.data


def test_corrupt_settings_preserved(tmp_path):
    (tmp_path / "settings.json").write_text("bad json")
    with pytest.raises(ValueError, match="backup saved"):
        Settings(tmp_path)
    assert list(tmp_path.glob("settings.corrupt-*.json"))


def test_mp4_mux_has_audio_and_video(tmp_path):
    video = tmp_path / "video.mp4"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30, (160, 120)
    )
    assert writer.isOpened()
    for _ in range(30):
        writer.write(np.full((120, 160, 3), 100, np.uint8))
    writer.release()
    with wave.open(str(tmp_path / "audio.wav"), "wb") as output:
        output.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
        output.writeframes(
            (
                np.sin(np.arange(44100) * 440 * 2 * np.pi / 44100)[:, None]
                * np.ones((1, 2))
                * 3000
            )
            .astype("<i2")
            .tobytes()
        )
    mux_video(video, tmp_path / "audio.wav", tmp_path / "result.mp4")
    result = subprocess.run(
        [
            ffmpeg(),
            "-i",
            str(tmp_path / "result.mp4"),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    assert b"Video: h264" in result.stderr and b"Audio: aac" in result.stderr
    decoded = np.frombuffer(result.stdout, dtype="<i2")
    assert len(decoded) > 80000 and np.max(np.abs(decoded)) > 100
    capture = cv2.VideoCapture(str(tmp_path / "result.mp4"))
    assert capture.get(cv2.CAP_PROP_FRAME_COUNT) / capture.get(
        cv2.CAP_PROP_FPS
    ) == pytest.approx(1, abs=0.08)
    capture.release()


def test_choke_trims_render_and_midi(tmp_path):
    now = [0.0]
    timeline = Timeline(lambda: now[0])
    sample = tmp_path / "long.wav"
    with wave.open(str(sample), "wb") as output:
        output.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
        output.writeframes(np.full((44100, 2), 1000, np.int16).tobytes())
    timeline.start()
    timeline.hit(
        pad="Crash",
        pad_id="crash",
        device="test",
        channel=10,
        note=49,
        velocity=127,
        sample=str(sample),
        gain=1,
    )
    now[0] = 0.1
    timeline.choke("crash")
    now[0] = 0.5
    timeline.stop()
    timeline.render(tmp_path / "choked.wav")
    data = read_sample(tmp_path / "choked.wav")
    assert np.max(np.abs(data[:2000])) > 0
    assert np.max(np.abs(data[4410:])) == 0
    timeline.export_midi(tmp_path / "choked.mid")
    assert any(
        m.type == "polytouch" for m in mido.MidiFile(tmp_path / "choked.mid").tracks[0]
    )


def test_velocity_layers_and_backing_mix(tmp_path):
    settings = Settings(tmp_path / "settings")
    pad = settings.pads[0]
    soft = tmp_path / "soft.wav"
    hard = tmp_path / "hard.wav"
    for path, level in [(soft, 1000), (hard, 3000)]:
        with wave.open(str(path), "wb") as output:
            output.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
            output.writeframes(np.full((44100, 2), level, np.int16).tobytes())
    settings.data["layers"] = {
        "Default": {
            pad["id"]: [
                dict(max_velocity=63, sample=str(soft)),
                dict(max_velocity=127, sample=str(hard)),
            ]
        }
    }
    settings.save()
    assert settings.sample(pad["id"], 40) == str(soft)
    assert settings.sample(pad["id"], 100) == str(hard)
    settings.export_bundle(tmp_path / "layers.zip")
    restored = Settings(tmp_path / "restored")
    restored.import_bundle(tmp_path / "layers.zip")
    assert Path(restored.sample(pad["id"], 40)).is_file()
    timeline = Timeline()
    timeline.backing = str(soft)
    timeline.backing_gain = 0.5
    timeline.duration = 0.5
    timeline.render(tmp_path / "backed.wav")
    data = read_sample(tmp_path / "backed.wav")
    assert len(data) == 22050
    assert np.mean(data) == pytest.approx(500 / 32768, abs=0.001)


def test_relocated_bundled_samples_after_pad_rename(tmp_path):
    settings = Settings(tmp_path)
    pad = settings.pads[0]
    pad["name"] = "Renamed kick"
    settings.data["sets"]["My set"] = dict(settings.data["sets"]["Default"])
    settings.data["sets"]["My set"][
        pad["id"]
    ] = "/no-longer-present/_MEI123/default_sounds/kick.wav"
    settings.data["sound_set"] = "My set"
    settings.save()
    restored = Settings(tmp_path)
    assert Path(restored.sample(pad["id"])).is_file()
    assert restored.pads[0]["name"] == "Renamed kick"


def test_import_keeps_filenames_without_overwriting_same_named_samples(tmp_path):
    settings = Settings(tmp_path / "settings")
    source = Path(settings.sample(settings.pads[0]["id"]))
    first = Path(settings.import_sample(source))
    second = Path(settings.import_sample(source))
    assert first.name == second.name == source.name
    assert first != second
    assert first.read_bytes() == second.read_bytes() == source.read_bytes()
    settings.data["sets"]["Default"][settings.pads[0]["id"]] = str(first)
    settings.export_bundle(tmp_path / "presets.zip")
    restored = Settings(tmp_path / "restored")
    restored.import_bundle(tmp_path / "presets.zip")
    assert Path(restored.sample(settings.pads[0]["id"])).name == source.name
