"""AddGram Raspberry Pi agent.

Every second: read the HX711, post the live weight to the backend, and check
for a tare command. When a NEW item sits stable on the scale it takes ONE
photo and posts it to /measurement. No photos while the scale is empty or
while the same item stays on it.

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
MIN_ITEM_G = 10.0        # an item must weigh at least this to be photographed
NEW_ITEM_DELTA_G = 15.0  # stable change this big vs. last photo = new item added/swapped
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


def close_camera():
    if _camera is not None:
        _camera.stop()
        _camera.close()


# ---------------------------------------------------------------- backend

def send_measurement(weight_g, image_bytes):
    response = requests.post(
        f"{BACKEND_URL}/measurement",
        data={"weight_g": str(round(weight_g, 1))},
        files={"image": ("capture.jpg", image_bytes, "image/jpeg")},
        timeout=30,
    )
    response.raise_for_status()
    print("Backend result:", response.json())


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
    """Cycles: empty 5 s -> 150 g item 8 s -> empty 5 s -> 230 g item 8 s ..."""
    CALIBRATED = True

    class HX711NotReady(RuntimeError):
        pass

    def __init__(self):
        pattern = [0.0] * 5 + [150.0] * 8 + [0.0] * 5 + [230.0] * 8
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
    """Decides when to take a photo: once per new, stable, non-zero item."""

    def __init__(self):
        self.recent = deque(maxlen=STABLE_SAMPLES)
        self.last_photo_weight = None  # None = scale was empty since last photo

    def reset(self):
        self.recent.clear()
        self.last_photo_weight = None

    def update(self, weight):
        """Feed one reading; return True when a photo should be taken now."""
        self.recent.append(weight)

        if weight < EMPTY_G:
            self.last_photo_weight = None  # item removed -> ready for the next one
            return False

        stable = (len(self.recent) == STABLE_SAMPLES
                  and max(self.recent) - min(self.recent) <= STABLE_DELTA_G)
        if not stable or weight < MIN_ITEM_G:
            return False

        if self.last_photo_weight is None:
            return True  # first stable item since the scale was empty
        return abs(weight - self.last_photo_weight) >= NEW_ITEM_DELTA_G

    def photo_taken(self, weight):
        self.last_photo_weight = weight


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

    use_camera = not args.no_camera
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

            if get_command() == "tare":
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

                if guard.update(weight):
                    if use_camera:
                        print(f"Stable new item: {weight:.1f} g -> taking photo")
                        try:
                            send_measurement(weight, capture_jpeg())
                        except Exception as exc:
                            # Not retried for the same item, so a broken camera
                            # can't flood the backend. Lift and replace to retry.
                            print("Measurement upload failed:", exc)
                    else:
                        print(f"Stable new item: {weight:.1f} g (camera off, no photo)")
                    guard.photo_taken(weight)

            # Keep a steady 1 s period regardless of how long the read took.
            time.sleep(max(0.0, POLL_SECONDS - (time.monotonic() - loop_start)))
    except KeyboardInterrupt:
        print("Stopping.")
    finally:
        close_camera()
        sensor.close()


if __name__ == "__main__":
    main()
