import sys
import time
import mido
import pygame
from tkinter import *

from tkinter import messagebox
import tkinter as tk


def choose_options(options:list):
  root = tk.Tk()
  root.title("Choose a number")
  root.geometry("300x200")
  root.resizable(False, False)

  selected_number = tk.IntVar(value=0)

  tk.Label(
    root,
    text="Choose a device with port:",
    font=("Arial", 12)
  ).pack(pady=15)

  button_frame = tk.Frame(root)
  button_frame.pack(pady=5)

  def select_number(number):
    selected_number.set(number)
    root.destroy()

  for number in range(1, len(options)):
    tk.Button(
      button_frame,
      text=options[number],
      width=4,
      height=2,
      command=lambda n=number: select_number(n)
    ).grid(
      row=(number - 1) // 5,
      column=(number - 1) % 5,
      padx=4,
      pady=4
    )

  root.mainloop()
  return selected_number.get()


# Initialize Pygame and Mixer
pygame.init()
pygame.mixer.init(frequency=44100, size=-16, channels=2)

# Screen setup
WIDTH, HEIGHT = 1000, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("MIDI Drum Visualizer & Recorder")
clock = pygame.time.Clock()

# Colors
BG_COLOR = (20, 20, 30)
GRID_COLOR = (50, 50, 70)
TEXT_COLOR = (220, 220, 220)
PLAYHEAD_COLOR = (255, 50, 50)
HIT_COLOR = (0, 200, 255)

# General MIDI Drum Mapping & Lane Setup
# Format: MIDI Note: (Display Name, Sound File, Lane Y-Index)
DRUM_MAP = {
  36: {"name": "Kick", "file": "kick.wav", "lane": 0},
  38: {"name": "Snare", "file": "snare.wav", "lane": 1},
  42: {"name": "Closed Hi-Hat", "file": "hihat_closed.wav", "lane": 2},
  44: {"name": "Pedal Hi-Hat", "file": "hihat_pedal.wav", "lane": 3},
  46: {"name": "Open Hi-Hat", "file": "hihat_open.wav", "lane": 4},
  50: {"name": "High Tom", "file": "tom1.wav", "lane": 5},
  48: {"name": "Mid Tom", "file": "tom2.wav", "lane": 6},
  43: {"name": "Floor Tom", "file": "floor.wav", "lane": 7},
  49: {"name": "Crash", "file": "crash.wav", "lane": 8},
  51: {"name": "Ride", "file": "ride.wav", "lane": 9},
}

# Load sounds (fallbacks gracefully if files are missing)
loaded_sounds = {}
for note, data in DRUM_MAP.items():
  try:
    loaded_sounds[note] = pygame.mixer.Sound(data["file"])
  except Exception:
    loaded_sounds[note] = None

FONT = pygame.font.SysFont("Arial", 16)


def select_midi_input():
  """Lists available MIDI inputs in console and lets user choose."""
  ports = mido.get_input_names()
  if not ports:

    messagebox.showinfo(title="Uh oh", message="No MIDI input devices found! Please connect your e-drum kit.")

    return None

  print("\nAvailable MIDI Input Devices:")
  selected = choose_options(ports)

  choice = selected
  return ports[choice]


def draw_interface(recording, current_time, max_duration=10.0):
  """Draws lanes, recorded hits, and the playhead."""
  screen.fill(BG_COLOR)

  lane_height = HEIGHT / len(DRUM_MAP)

  # Draw lanes and labels
  for note, data in DRUM_MAP.items():
    y = data["lane"] * lane_height
    pygame.draw.line(screen, GRID_COLOR, (0, y), (WIDTH, y), 1)

    # Render lane label
    label = FONT.render(data["name"], True, TEXT_COLOR)
    screen.blit(label, (10, y + lane_height / 2 - 10))

  # Draw timeline scaling (Pixels per second)
  timeline_width = WIDTH - 150
  start_x = 120

  # Draw hits
  for hit in recording:
    if hit["note"] in DRUM_MAP:
      lane = DRUM_MAP[hit["note"]]["lane"]
      # Map time to X coordinate (assuming 10 seconds view window, scrolling or scaling)
      x = start_x + (hit["time"] / max(max_duration, 10.0)) * timeline_width
      y = lane * lane_height + lane_height / 2
      pygame.draw.circle(screen, HIT_COLOR, (int(x), int(y)), 6)

  # Draw playhead
  playhead_x = (
      start_x + (current_time / max(max_duration, 10.0)) * timeline_width
  )
  pygame.draw.line(
      screen,
      PLAYHEAD_COLOR,
      (playhead_x, 0),
      (playhead_x, HEIGHT),
      2,
  )

  pygame.display.flip()


def main():
  port_name = select_midi_input()
  if not port_name:
    return

  print(f"\nConnected to {port_name}!")
  print("Controls inside Pygame window:")
  print("- Press [R] to start/stop Recording")
  print("- Press [P] to Play back recording")
  print("- Close window to exit\n")

  recording = []
  state = "IDLE"  # IDLE, RECORDING, PLAYBACK
  start_time = 0
  playback_start = 0
  event_index = 0
  max_duration = 10.0

  running = True
  while running:
    current_wall_time = time.time()

    for event in pygame.event.get():
      if event.type == pygame.QUIT:
        running = False
      elif event.type == pygame.KEYDOWN:
        if event.key == pygame.K_r:
          recording = []
          start_time = time.time()
          state = "RECORDING"
          print("--- RECORDING STARTED ---")
        elif event.key == pygame.K_p and recording:
          playback_start = time.time()
          event_index = 0
          state = "PLAYBACK"
          print("--- PLAYBACK STARTED ---")

    # Handle MIDI input during recording
    if state == "RECORDING":
      with mido.open_input(port_name) as inport:
        for msg in inport.iter_pending():
          if msg.type == "note_on" and msg.velocity > 0:
            t = time.time() - start_time
            recording.append(
                {"time": t, "note": msg.note, "velocity": msg.velocity}
            )
            print(f"{msg.note} - {msg.velocity}")
            if t > max_duration:
              max_duration = t + 5  # Expand timeline dynamically

      current_time = time.time() - start_time

    elif state == "PLAYBACK":
      current_time = time.time() - playback_start

      # Trigger hits during playback
      while event_index < len(recording) and current_time >= recording[event_index]["time"]:
        hit = recording[event_index]
        note = hit["note"]
        velocity = hit["velocity"]

        if note in loaded_sounds and loaded_sounds[note]:
          volume = velocity / 127.0
          loaded_sounds[note].set_volume(volume)
          loaded_sounds[note].play()

        event_index += 1

      if event_index >= len(recording):
        state = "IDLE"
        print("Playback finished.")
    else:
      current_time = 0

    draw_interface(recording, current_time, max_duration)
    clock.tick(60)

  pygame.quit()
  sys.exit()


if __name__ == "__main__":
  main()