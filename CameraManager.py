import cv2
import time
import path

class CameraManager:
    def __init__(self):
        self.cap = None
        self.camera_index = None
        self.recording_writer = None
        self.recording_path = None
        self.last_frame = None
        self.last_record_time = 0.0
        self.record_fps = 30.0

    @staticmethod
    def list_cameras(max_cameras=10):
        cameras = []

        if cv2 is None:
            return cameras

        for index in range(max_cameras):
            cap = cv2.VideoCapture(index)

            if cap.isOpened():
                cameras.append((index, f"Camera {index}"))

            cap.release()

        return cameras

    def open(self, index):
        self.close()

        if cv2 is None:
            return False

        self.cap = cv2.VideoCapture(int(index))

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
            width=40,
            height=18
        )
        self.preview.pack(pady=10)

        ttk.Button(
            self,
            text="Refresh Cameras",
            command=self.refresh_cameras
        ).pack(fill="x")

        self.refresh_cameras()

    def refresh_cameras(self):
        cameras = CameraManager.list_cameras()

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

    def get_selected_index(self):
        try:
            return int(self.camera_var.get().split(":")[0])
        except (ValueError, IndexError):
            return None

    def change_camera(self, event=None):
        self.stop()
        self.start()

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

            frame = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2RGB
            )

            image = Image.fromarray(frame)
            image.thumbnail((360, 280))

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