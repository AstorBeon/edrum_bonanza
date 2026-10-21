# E-Drum Bonanza

A local Python/Qt desktop studio for an electronic drum kit. No browser, account,
or cloud service is required to use the application.

## Run

Use Python 3.12 (64-bit recommended). From the repository root:

```sh
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS instead: source .venv/bin/activate
python -m pip install -r requirements.txt
python EDrumBonanza.py
```

On Linux, Qt needs a graphical desktop and the system libraries for its platform
plugin; MIDI needs an ALSA sequencer device. Missing MIDI/audio hardware does not
prevent keyboard simulation or offline exports. This cloud environment has its
Python environment at `/workspace/.venvs/edrum`.

## Workflow

- **Test:** choose a MIDI input, kit preset and sound set; hit the kit to highlight
  pads, hear samples, and inspect device/channel/note/velocity logs. Click pads or
  use keys `1`–`9`, `0` to simulate hits. Refresh scans MIDI inputs and camera indices
  0–4. Enable camera preview after choosing a camera.
- **Record:** uses the selections on Test. Record, pause/resume and stop share a
  monotonic timeline. Enable video before recording to capture the selected
  camera. Choose tempo, quarter-note beats per bar, optional count-in and
  metronome. Metronome is monitoring only and is excluded from exports. Load a
  backing WAV for playback during the take; optionally include it in WAV/MP4
  exports. Backing playback follows pause/resume and has its own volume slider.
- **Export:** save WAV, MP4 (for a take recorded with video), CSV, JSON or MIDI.
  Files are rendered in a background thread and replaced only after success.
  Existing files require the native save-dialog overwrite confirmation. Use
  Output folder to remember a preferred location. MP4 includes synthesized drum
  audio, not microphone audio. WAV retains sample tails; MP4 ends with the video.
- **Options:** duplicate/delete presets, add/remove inputs and edit pad names,
  MIDI notes, device names, channels, gain and sensitivity. Channel `0` and device
  `*` match any channel/device. For Learn, select a row and strike the desired
  physical pad, then click **Save mappings**. Save edits before switching presets.
  Create/delete sound sets, import PCM WAV samples per input, and preview them.
  Assigning a WAV replaces any velocity layers for that input. Imported samples
  keep their original filenames in separate managed folders. Sample columns show
  filenames with full paths on hover; drag column-header borders to resize.
  Velocity layers assigns multiple WAVs with ascending velocity thresholds ending
  at 127. The first threshold at or above the adjusted hit velocity selects the
  sample. Remove all layers to return to the regular WAV assignment.
- **Portable presets:** export/import a ZIP containing all presets, sound sets and
  available samples. Import replaces the configuration after validation. Export
  first if you want a backup. Missing samples are silent and reported in the log.
- **About:** describes the app and keyboard controls.

Settings and imported WAV files live in `%APPDATA%\EDrumBonanza` on Windows,
`~/.local/share/EDrumBonanza` on Linux, or
`~/Library/Application Support/EDrumBonanza` on macOS. Settings are saved with an
atomic replacement and never rewrite application source. Invalid settings are
backed up and startup reports the backup location. Original sample imports are
copied, so moving the original WAV does not break the set.

## MIDI and recording details

Mappings match MIDI input device + channel + note. Velocity-zero note-on messages
are ignored. Velocity, sensitivity and per-pad/master gain affect monitoring and
rendering. Simultaneous notes are mixed; final audio is clipped to PCM range, so
reduce levels if the mix distorts. Supported WAV samples are mono/stereo integer
PCM, 8/16/24/32 bit; sample rates are converted to 44.1 kHz stereo.

For the General MIDI hi-hat convention, CC4 values below 64 route note 42 to
open-hat note 46; higher values select closed. A closed-hat hit chokes the open
hat. Polyphonic aftertouch with a nonzero value chokes the matching pad. Recorded
chokes affect WAV/MP4 and are included in JSON and MIDI. CSV is a hit-only log.
Kits using other controller/choke conventions need a device-specific adapter.

JSON/CSV contain elapsed seconds and calculated bar/beat positions at the chosen
tempo. The signature denominator is currently quarter notes. MIDI exports retain
channels, notes and velocities with short note gates; they do not embed samples.
Pauses exclude time from both video and MIDI/hits. Camera frames are read on a
background thread; slow/missing frames hold the last frame to keep the output
video on the audio timeline. No configurable hardware latency compensation is
currently provided. Verify synchronization and responsiveness on your actual kit.

Takes are held for the current session. New recordings and closing prompt before
discarding a take; export the formats you need before closing. Imported samples
and configuration persist across reboots. Automatic crash recovery of unsaved
takes is not implemented.

## Build a Windows executable

Build **on Windows** with Python 3.12; Linux PyInstaller cannot create a Windows EXE.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m PyInstaller --noconfirm EDrumBonanza.spec
.\dist\EDrumBonanza.exe
```

The `Windows desktop build` GitHub Actions workflow also runs tests and uploads
a Windows executable artifact on pushes/PRs or manual dispatch. It has been
added here but has not been run from this cloud session.

The spec bundles default WAV files, the MIDI backend, and the FFmpeg binary from
`imageio-ffmpeg`. A system FFmpeg is used when available, otherwise the bundled
binary is used. There is no runtime download. The spec creates a single-file GUI
application. Existing draft artifacts in `dist/` and `build/` predate this rewrite;
rebuild before distributing them. The bundled FFmpeg build's licenses should be
included when distributing an application containing that binary.

## Development and validation

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
# Headless Linux:
QT_QPA_PLATFORM=offscreen SDL_AUDIODRIVER=dummy python -m pytest -q
```

Tests exercise persisted/portable settings, pause timing, non-silent audio, MIDI
and event export, recorded choking, GUI simulation/Learn, camera timing through
pauses, and an MP4 with decodable audio and video. Tests use temporary data folders
and no physical devices. They do not validate real MIDI latency, webcam drivers,
audio hardware, or a Windows executable.

Code layout: `edrum/core.py` owns settings, timeline and exports;
`edrum/devices.py` owns background camera acquisition; `edrum/app.py` owns the Qt
interface and device routing; `EDrumBonanza.py` is the launch entry point.

Metronome, count-in, WAV backing tracks and multiple velocity-layer samples are
included. Backing-track selection, recording preferences, presets and layers persist.
