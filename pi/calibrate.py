"""Calibrate the AddGram scale with a known weight.

Run on the Pi (stop pi_agent.py first, only one program may use the GPIO pins):
    python3 calibrate.py            # asks for the known weight
    python3 calibrate.py 500        # known weight = 500 g

Saves the factor to calibration.json next to sensor_adapter.py,
which the adapter loads automatically on start.
"""

import statistics
import sys

import sensor_adapter as sa


def main():
    s = sa.sensor
    print(f"Using DOUT=BCM{s.dout}, SCK=BCM{s.sck}")

    try:
        input("1) Leave ONLY the top plate on the scale, then press Enter...")
        offset = s.tare(count=25)
        print(f"   Empty offset (raw): {offset:.0f}")

        if len(sys.argv) > 1:
            known_g = float(sys.argv[1])
        else:
            known_g = float(input("2) Known weight in grams (e.g. 500): "))
        if known_g <= 0:
            raise ValueError("known weight must be > 0")

        input(f"3) Put the {known_g:g} g weight in the middle of the plate, wait 2 s, press Enter...")
        loaded = statistics.median([s.read_median(5) for _ in range(5)])
        delta = loaded - offset
        print(f"   Loaded (raw): {loaded:.0f}   difference: {delta:.0f}")

        if abs(delta) < 100:
            print("ERROR: almost no change in the reading. The load cell is probably "
                  "not wired to A+/A- correctly, or the weight isn't on the cell.")
            return 1

        factor = delta / known_g  # raw units per gram (may be negative; that's fine)
        sa.save_calibration(factor, known_g)
        print(f"Saved factor {factor:.3f} raw/g to {sa.CALIBRATION_FILE}")

        s.scale = factor
        print("Check: reading now =", round(s.read_weight_grams(), 1), "g")
        return 0
    except sa.HX711NotReady as exc:
        print("ERROR:", exc)
        return 1
    finally:
        sa.close()


if __name__ == "__main__":
    sys.exit(main())
