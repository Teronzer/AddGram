"""AddGram Raspberry Pi agent.

Every second: read the HX711, post the live (total) weight to the backend,
and check for a tare command. When food is ADDED and the weight is stable it
takes ONE photo and posts only the added portion to /measurement:
90 g pasta, then 20 g tomatoes on top -> two entries, 90 g and 20 g.
Removing food never creates an entry, it only lowers the baseline.
No photos while the scale is empty or while nothing changes.
The web page can also ask for a photo right now (TEMPORARY test button,
"photo" command), see take_manual_photo().

    python3 pi_agent.py              # real HX711 + camera
    python3 pi_agent.py --simulate   # fake weights, no hardware needed
    python3 pi_agent.py --no-camera  # real HX711, live weight only
"""

import argparse
import io
import itertools
import os
import time
from collections import deque

import requests

BACKEND_URL = os.getenv("ADDGRAM_BACKEND", "http://172.20.10.11:8000")  # laptop's LAN IP
POLL_SECONDS = 1.0

# Photo guard (grams)
STABLE_SAMPLES = 3       # readings in a row that must agree (3 s at 1 s polling)
STABLE_DELTA_G = 2.0     # max spread between those readings
EMPTY_G = 5.0            # below this the scale counts as empty -> ready for next item
MIN_ITEM_G = 10.0        # an added portion must weigh at least this to be photographed
ZERO_SNAP_G = 1.0        # |w| below this is shown as 0.0


# ---------------------------------------------------------------- camera

_camera = None


def capture_jpeg():
    """Take one JPEG. The camera is opened once and reused
    (opening a new Picamera2 every time fails with 'camera busy')."""
    global _camera
    from picamera2 import Picamera2
    if _camera is None:
        _camera = Picamera2()
        _camera.configure(_camera.create_still_configuration(main={"size": (1280, 960)}))
        _camera.start()
        time.sleep(1.5)  # auto-exposure settle, only on first use
    stream = io.BytesIO()
    _camera.capture_file(stream, format="jpeg")
    return stream.getvalue()


def fake_jpeg(weight):
    """--simulate only: a plain coloured picture instead of a camera photo."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (640, 480), (230, 160, 60))
    ImageDraw.Draw(img).text((20, 20), f"SIMULATED {weight or 0:.1f} g", fill=(0, 0, 0))
    stream = io.BytesIO()
    img.save(stream, format="JPEG")
    return stream.getvalue()


def close_camera():
    if _camera is not None:
        _camera.stop()
        _camera.close()


# ---------------------------------------------------------------- backend

def send_measurement(portion_g, total_g, image_bytes, source="pi-auto"):
    """portion_g = food just added (used for nutrition); total_g = whole scale."""
    response = requests.post(
        f"{BACKEND_URL}/measurement",
        data={"weight_g": str(round(portion_g, 1)),
              "total_weight_g": str(round(total_g, 1)),
              "source": source},
        files={"image": ("capture.jpg", image_bytes, "image/jpeg")},
        timeout=30,
    )
    response.raise_for_status()
    print("Backend result:", response.json())


def send_photo_only(total_g, image_bytes):
    """Photo without a meal entry (manual photo of an empty scale)."""
    data = {"source": "pi-manual"}
    if total_g is not None:
        data["total_weight_g"] = str(round(total_g, 1))
    response = requests.post(
        f"{BACKEND_URL}/photo",
        data=data,
        files={"image": ("capture.jpg", image_bytes, "image/jpeg")},
        timeout=30,
    )
    response.raise_for_status()
    print("Photo saved on backend (no meal entry).")


def send_live_weight(weight_g):
    try:
        requests.post(
            f"{BACKEND_URL}/weight/update",
            json={"weight_g": round(float(weight_g), 1)},
            timeout=3,
        ).raise_for_status()
    except requests.RequestException as exc:
        print("Live weight upload failed:", exc)


def get_command():
    try:
        return requests.get(f"{BACKEND_URL}/command", timeout=3).json().get("command") or ""
    except (requests.RequestException, ValueError) as exc:
        print("Command polling failed:", exc)
        return ""


# ---------------------------------------------------------------- sensors

class SimulatedSensor:
    """Cycles: empty 5 s -> 90 g pasta -> +20 g tomatoes -> 70 g removed -> empty ..."""
    CALIBRATED = True

    class HX711NotReady(RuntimeError):
        pass

    def __init__(self):
        pattern = [0.0] * 5 + [90.0] * 6 + [110.0] * 6 + [40.0] * 6
        self._values = itertools.cycle(pattern)

    def read_weight_grams(self):
        return next(self._values)

    def tare(self):
        return 0.0

    def close(self):
        pass


def tare_with_retry(sensor, not_ready_error):
    """Auto-tare at startup: zero out the top plate. Keeps retrying until
    the HX711 answers so a loose wire gives a clear message, not a crash."""
    while True:
        try:
            print("Auto-tare: keep ONLY the top plate on the scale...")
            offset = sensor.tare()
            print(f"Auto-tare done (offset {offset}).")
            return
        except not_ready_error as exc:
            print(f"ERROR during auto-tare: {exc}\n  Retrying in 3 s (Ctrl+C to quit).")
            time.sleep(3)


# ---------------------------------------------------------------- photo guard

class PhotoGuard:
    """Decides when to take a photo and how much food was added.

    `baseline` is the last stable total weight that has been accounted for
    (0 when the scale is empty). A stable increase of at least MIN_ITEM_G
    over the baseline is a new portion; a stable decrease (food removed)
    just moves the baseline down without creating an entry.
    """

    def __init__(self):
        self.recent = deque(maxlen=STABLE_SAMPLES)
        self.baseline = 0.0

    def reset(self):
        self.recent.clear()
        self.baseline = 0.0

    def update(self, weight):
        """Feed one total reading. Returns the added portion in grams when a
        photo should be taken now, otherwise None."""
        self.recent.append(weight)

        if weight < EMPTY_G:
            self.baseline = 0.0  # scale emptied -> start over
            return None

        stable = (len(self.recent) == STABLE_SAMPLES
                  and max(self.recent) - min(self.recent) <= STABLE_DELTA_G)
        if not stable:
            return None

        added = weight - self.baseline
        if added >= MIN_ITEM_G:
            return added
        if added < -STABLE_DELTA_G:
            self.baseline = weight  # food removed: lower the baseline, no entry
        return None

    def portion_logged(self, total_weight):
        self.baseline = total_weight

    def manual_portion(self, weight):
        """Weight to log for a photo requested from the web page.
        Food added since the baseline -> that portion; nothing new added ->
        the whole scale total; empty scale -> None (photo only, no entry)."""
        if weight is None or weight < EMPTY_G:
            return None
        added = weight - self.baseline
        return added if added >= EMPTY_G else weight


# ---------------------------------------------------------------- manual photo

def take_manual_photo(guard, weight, capture, weights_valid):
    """TEMPORARY (testing): photo requested by the "Take photo now" button.
    Remove together with that button during cleanup."""
    portion = guard.manual_portion(weight) if weights_valid else None
    shown = f"{weight:.1f} g" if weight is not None else "unknown"
    print(f"Photo requested from web page (scale total {shown}) -> taking photo")
    try:
        image = capture(weight)
        if portion is None:
            send_photo_only(weight if weights_valid else None, image)
        else:
            print(f"Logging {portion:.1f} g from the manual photo")
            send_measurement(portion, weight, image, source="pi-manual")
            guard.portion_logged(weight)
    except Exception as exc:
        print("Manual photo failed:", exc)


# ---------------------------------------------------------------- main loop

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true", help="fake weights, no hardware")
    parser.add_argument("--no-camera", action="store_true", help="never take photos")
    args = parser.parse_args()

    if args.simulate:
        sensor = SimulatedSensor()
        not_ready_error = SimulatedSensor.HX711NotReady
    else:
        import sensor_adapter as sensor
        not_ready_error = sensor.HX711NotReady

    camera_present = not args.no_camera
    use_camera = camera_present
    # In --simulate there is no camera: send a generated picture instead.
    capture = fake_jpeg if args.simulate else (lambda _weight: capture_jpeg())
    if not sensor.CALIBRATED and use_camera:
        print("Scale NOT calibrated -> auto photos disabled until you run calibrate.py.")
        use_camera = False

    print(f"Backend: {BACKEND_URL}")
    tare_with_retry(sensor, not_ready_error)

    guard = PhotoGuard()
    failures = 0

    try:
        while True:
            loop_start = time.monotonic()

            command = get_command()
            if command == "tare":
                tare_with_retry(sensor, not_ready_error)
                guard.reset()

            try:
                weight = float(sensor.read_weight_grams())
                if failures:
                    print(f"HX711 is answering again after {failures} failed reads.")
                failures = 0
            except not_ready_error as exc:
                failures += 1
                # Log the first failure and then every 10th so the console stays readable.
                if failures == 1 or failures % 10 == 0:
                    print(f"ERROR reading HX711 ({failures} in a row): {exc}")
                weight = None

            if weight is not None:
                if abs(weight) < ZERO_SNAP_G:
                    weight = 0.0
                send_live_weight(weight)

                portion = guard.update(weight)
                if portion is not None:
                    if use_camera:
                        print(f"Added {portion:.1f} g (scale total {weight:.1f} g) -> taking photo")
                        try:
                            send_measurement(portion, weight, capture(weight))
                        except Exception as exc:
                            # Not retried for the same item, so a broken camera
                            # can't flood the backend. Lift and replace to retry.
                            print("Measurement upload failed:", exc)
                        if command == "photo":
                            command = ""  # this automatic photo already covers it
                    else:
                        print(f"Added {portion:.1f} g (scale total {weight:.1f} g), camera off, no photo")
                    guard.portion_logged(weight)

            if command == "photo":  # TEMPORARY test button on the web page
                if camera_present:
                    take_manual_photo(guard, weight, capture, sensor.CALIBRATED)
                else:
                    print("Photo requested, but the camera is off (--no-camera).")

            # Keep a steady 1 s period regardless of how long the read took.
            time.sleep(max(0.0, POLL_SECONDS - (time.monotonic() - loop_start)))
    except KeyboardInterrupt:
        print("Stopping.")
    finally:
        close_camera()
        sensor.close()


if __name__ == "__main__":
    main()
