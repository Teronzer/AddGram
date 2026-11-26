"""HX711 load-cell driver for the AddGram scale.

Wiring (BCM numbering, NOT physical pin numbers):
    HX711 DT / DOUT -> GPIO 5  (physical pin 29)
    HX711 SCK       -> GPIO 6  (physical pin 31)
    HX711 VCC       -> 3.3 V   (physical pin 1)   keep DOUT at 3.3 V logic!
    HX711 GND       -> GND     (physical pin 6)
Override the pins with env vars HX711_DOUT / HX711_SCK if you wired differently.

Calibration is stored in calibration.json next to this file
(created by calibrate.py). Run this file directly for a quick diagnostic:
    python3 sensor_adapter.py
"""

import json
import os
import statistics
import time
from pathlib import Path

import RPi.GPIO as GPIO  # on a Pi 5 install python3-rpi-lgpio (drop-in replacement)

CALIBRATION_FILE = Path(__file__).with_name("calibration.json")

DOUT_PIN = int(os.getenv("HX711_DOUT", "5"))
SCK_PIN = int(os.getenv("HX711_SCK", "6"))

# Raw values the HX711 returns when the input is saturated / wiring is broken.
RAW_MAX = 0x7FFFFF
RAW_MIN = -0x800000


class HX711NotReady(RuntimeError):
    """Raised when the chip isn't answering or only returns garbage."""


class HX711:
    # Extra SCK pulses after the 24 data bits select the channel/gain
    # for the NEXT reading: 1 -> A/128, 2 -> B/32, 3 -> A/64.
    GAIN_PULSES = {128: 1, 32: 2, 64: 3}

    def __init__(self, dout=DOUT_PIN, sck=SCK_PIN, gain=128):
        if gain not in self.GAIN_PULSES:
            raise ValueError("gain must be 128, 64 or 32")
        self.dout = dout
        self.sck = sck
        self.gain = gain
        self.offset = 0.0
        self.scale = 1.0

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.sck, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.dout, GPIO.IN)

        # If SCK was left HIGH by a crashed run, the chip is powered down.
        # Holding SCK LOW wakes it up; give it time to settle.
        self.power_up()
        # One throw-away read so the gain selection takes effect.
        try:
            self.read_raw(timeout=1.0)
        except HX711NotReady:
            pass  # reported properly on the first real read

    def power_up(self):
        GPIO.output(self.sck, GPIO.LOW)
        time.sleep(0.5)

    def is_ready(self):
        return GPIO.input(self.dout) == GPIO.LOW

    def _read_once(self):
        """Clock out one 24-bit value. Caller made sure DOUT is LOW."""
        value = 0
        # Each SCK HIGH must stay under 60 us or the HX711 powers down.
        # RPi.GPIO calls take a few us, so this is fine unless Linux
        # preempts us mid-read; such reads are caught as invalid/outliers.
        for _ in range(24):
            GPIO.output(self.sck, GPIO.HIGH)
            GPIO.output(self.sck, GPIO.LOW)
            # DOUT is valid after the rising edge and stays valid while SCK
            # is low, so read it after dropping SCK (keeps HIGH time minimal).
            value = (value << 1) | GPIO.input(self.dout)

        for _ in range(self.GAIN_PULSES[self.gain]):
            GPIO.output(self.sck, GPIO.HIGH)
            GPIO.output(self.sck, GPIO.LOW)

        if value & 0x800000:  # two's complement
            value -= 1 << 24
        return value

    def read_raw(self, timeout=0.5, retries=3):
        """Return one raw reading, retrying obviously bad ones."""
        last_bad = None
        for _ in range(retries):
            start = time.monotonic()
            while not self.is_ready():
                if time.monotonic() - start > timeout:
                    raise HX711NotReady(
                        f"HX711 not ready: DOUT (BCM {self.dout}) stayed HIGH for "
                        f"{timeout:.1f}s. Check VCC/GND, that DT goes to BCM {self.dout} "
                        f"and SCK to BCM {self.sck} (BCM numbers, not physical pins), "
                        f"and that nothing else is using these pins."
                    )
                time.sleep(0.001)

            value = self._read_once()
            if value in (RAW_MAX, RAW_MIN, -1):
                # -1 (all ones) usually means DT is floating / not connected;
                # +-max means the load-cell input is saturated.
                last_bad = value
                continue
            return value

        raise HX711NotReady(
            f"HX711 returned only invalid raw values (last={last_bad}). "
            "-1 means DT is floating/disconnected; 8388607 or -8388608 means the "
            "load-cell wires (E+, E-, A+, A-) are wrong or the cell is overloaded."
        )

    def read_median(self, count=5):
        values = [self.read_raw() for _ in range(count)]
        return statistics.median(values)

    def tare(self, count=15):
        self.offset = self.read_median(count)
        return self.offset

    def read_weight_grams(self, count=5):
        return (self.read_median(count) - self.offset) / self.scale

    def close(self):
        GPIO.cleanup([self.dout, self.sck])


# ------------------------------------------------------------------
# Module-level API used by pi_agent.py and calibrate.py
# ------------------------------------------------------------------

def load_calibration():
    """Return the saved scale factor (raw units per gram), or None."""
    try:
        data = json.loads(CALIBRATION_FILE.read_text())
        factor = float(data["scale"])
        return factor if factor != 0 else None
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None


def save_calibration(scale, known_g):
    CALIBRATION_FILE.write_text(json.dumps({
        "scale": scale,
        "known_weight_g": known_g,
        "dout": sensor.dout,
        "sck": sensor.sck,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=2))


sensor = HX711()
_factor = load_calibration()
CALIBRATED = _factor is not None
sensor.scale = _factor if CALIBRATED else 1.0
if not CALIBRATED:
    print(f"WARNING: {CALIBRATION_FILE.name} not found - weights are RAW units, "
          "not grams. Run: python3 calibrate.py")


def read_weight_grams():
    return sensor.read_weight_grams()


def tare():
    return sensor.tare()


def close():
    sensor.close()


if __name__ == "__main__":
    # Quick hardware diagnostic: prints raw values and grams twice a second.
    print(f"Pins: DOUT=BCM{sensor.dout}, SCK=BCM{sensor.sck}; "
          f"scale={sensor.scale} ({'calibrated' if CALIBRATED else 'NOT calibrated'})")
    try:
        print("Taring (keep only the top plate on the scale)...")
        print("Offset:", tare())
        while True:
            raw = sensor.read_median(5)
            print(f"raw={raw:>10.0f}   weight={(raw - sensor.offset) / sensor.scale:>9.1f}")
            time.sleep(0.5)
    except HX711NotReady as exc:
        print("ERROR:", exc)
    except KeyboardInterrupt:
        pass
    finally:
        close()
