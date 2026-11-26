# AddGram – starter project

This starter gives you a working software structure for the AddGram prototype:
- FastAPI backend
- SQLite nutrition + meal database
- YOLO image-classification training and inference hooks
- phone-friendly web page
- Raspberry Pi camera/network agent skeleton
- example foods CSV

It is deliberately an MVP. First get the software working with simulated weight and a few sample images, then connect the real HX711 and camera.

## 1. Architecture

The Pi sends `weight_g` and a photo to `POST /measurement`. The backend runs the classifier, matches the predicted class to `foods` in SQLite, calculates nutrition, and stores the latest result. The phone polls `GET /latest`, lets the user correct the food, and posts confirmed portions to `POST /meal/add`.

Only the backend talks to SQLite and loads the AI model. The phone and Pi communicate with the backend over your local Wi-Fi.

## 2. Requirements

Development laptop:
- Python 3.10 or 3.11 recommended
- a terminal (PowerShell on Windows, Terminal on macOS/Linux)
- laptop and phone on the same Wi-Fi / hotspot

Raspberry Pi:
- Raspberry Pi OS
- Camera Module 3 enabled and tested
- Python 3, `picamera2`, and network access to the laptop

The YOLO training package is best installed on the laptop, not the Pi. A GPU is helpful but not required for a small classification model.

## 3. Create the Python environment

Open a terminal in this folder.

Windows:
```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

macOS/Linux:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If you only want to run the backend before training, install `requirements-core.txt` instead. Install `requirements-train.txt` when you are ready to train YOLO.

## 4. Create and populate the database

The database is a local file: `backend/addgram.db`. SQLite creates it automatically the first time the backend starts. You do not need to install a database server.

The startup code creates three tables:
- `foods`: one row per supported food, with nutrition per 100 g and a model class name
- `meals`: a meal/session
- `meal_items`: confirmed portions added to a meal

Edit `data/foods.csv`. Keep `class_name` exactly the same as the corresponding YOLO class folder name. Nutrition values must be per 100 g and the food state must be explicit (e.g. `rice_cooked`, not just `rice`). Then run:

```bash
python scripts/import_foods.py
```

You can rerun the importer after editing the CSV; it updates matching food rows. Do not mix raw and cooked nutrition values. Verify every value against its cited source (e.g. USDA FoodData Central) before relying on it.

## 5. Start the backend

From the project root, with the virtual environment active:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://127.0.0.1:8000/` on the laptop. API documentation is at `http://127.0.0.1:8000/docs`.

Find your laptop's local IPv4 address:
- Windows: `ipconfig`
- macOS: `ifconfig` or System Settings → Network
- Linux: `hostname -I`

On the phone, open `http://LAPTOP_IP:8000/` while connected to the same Wi-Fi/hotspot. Replace `LAPTOP_IP` with the actual address. Allow Python through the laptop firewall on private networks. University Wi-Fi may isolate devices; use a hotspot or travel router if needed.

The QR code should encode that phone-accessible URL, for example `http://192.168.1.20:8000/`. The IP may change, so reserve it in your router or regenerate the QR label when it changes. Do not expose this development server directly to the public internet.

## 6. Train the food classifier

### Choose classes
Start with 5 foods while proving the pipeline, then expand to 10–20. Each class should describe one state:
- `apple_raw`
- `banana_raw`
- `carrot_raw`
- `rice_cooked`
- `pasta_cooked`

The example CSV has example classes; change them to your actual foods and keep names identical everywhere.

### Collect and label photos
Use the finished camera position and lighting. Capture at least 50–100 images per class if possible, with different quantities, positions, plates and backgrounds. Avoid taking many nearly identical burst images.

Split by capture session, not random near-duplicates:
- 70% training
- 15% validation
- 15% test

This prevents nearly identical photos appearing in both training and test sets. Never use test photos to tune the model.

### Dataset folder layout

Create `datasets/addgram_foods/` like this:

```text
datasets/addgram_foods/
  train/
    apple_raw/
    banana_raw/
    carrot_raw/
    rice_cooked/
    pasta_cooked/
  val/
    apple_raw/
    banana_raw/
    carrot_raw/
    rice_cooked/
    pasta_cooked/
  test/
    apple_raw/
    banana_raw/
    carrot_raw/
    rice_cooked/
    pasta_cooked/
```

Put the relevant JPG/PNG images in each class folder. For Ultralytics classification, folder names are the labels.

### Install training dependencies and train

```bash
pip install -r requirements-train.txt
yolo version
python train/train_classifier.py --data datasets/addgram_foods --epochs 50 --imgsz 224
```

The script uses a small pretrained classification model (`yolo11n-cls.pt`) and fine-tunes it. Training outputs are normally saved under `runs/classify/addgram/weights/best.pt`. Copy the best weights to:

```text
backend/models/best.pt
```

Run the test script:

```bash
python train/evaluate_classifier.py --weights backend/models/best.pt --data datasets/addgram_foods/test
```

Record top-1 and top-3 accuracy. Your proposal's target is top-3 accuracy ≥80% on genuinely unseen test photos. Do not claim this target until measured.

If your installed Ultralytics version does not recognise the model name, update Ultralytics and check its current classification model names/documentation.

## 7. Connect the trained model to the backend

The backend looks for `backend/models/best.pt`. Set the optional environment variable `ADDGRAM_MODEL` to another path if needed.

When a Pi image arrives:
1. `/measurement` saves the image temporarily.
2. `backend/recognition.py` loads YOLO once and predicts top classes.
3. The backend matches each class to `foods.class_name`.
4. It calculates nutrition for the measured weight.
5. `/latest` returns the result to the phone.

If the model file is absent, the backend still works and returns no AI predictions. You can select a food manually in the web app. This is intentional: build and test each part independently.

## 8. Raspberry Pi setup

On the Pi, install/test Raspberry Pi OS camera support first. On current Raspberry Pi OS, `picamera2` is commonly installed through apt:

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-requests
```

Test the camera:
```bash
rpicam-hello --timeout 5000
```

Copy `pi/pi_agent.py`, `pi/sensor_adapter.py` and `pi/calibrate.py` to the Pi. Set the backend address with `export ADDGRAM_BACKEND=http://LAPTOP_IP:8000` (or edit `BACKEND_URL` in `pi_agent.py`).

HX711 wiring (BCM numbers): DT -> GPIO 5 (pin 29), SCK -> GPIO 6 (pin 31), VCC -> 3.3 V, GND -> GND. Override with `HX711_DOUT` / `HX711_SCK` env vars. On a Raspberry Pi 5 install `python3-rpi-lgpio` (RPi.GPIO does not work there).

```bash
python3 sensor_adapter.py        # diagnostic: raw values + weight, Ctrl+C to stop
python3 calibrate.py 500         # tare with empty plate, then put a known 500 g weight on
python3 pi_agent.py --no-camera  # live weight only
python3 pi_agent.py              # live weight + one photo per new stable item
python3 pi_agent.py --simulate   # no hardware
```

`calibrate.py` saves `calibration.json` next to the adapter. `pi_agent.py` auto-tares at startup (keep only the top plate on the scale), so the plate's weight is cancelled out. A photo is taken when food is added: the weight must be stable for 3 readings and at least 10 g above the baseline (what was already on the scale). Only the added portion is sent as `weight_g` (e.g. 90 g pasta, then 20 g tomatoes on top = entries of 90 g and 20 g); the whole scale reading goes along as `total_weight_g`. Removing food never creates an entry, it only lowers the baseline, and emptying the scale (below 5 g) resets it to 0. Without a calibration file, auto photos stay off.

## 9. End-to-end test without hardware

1. Start backend.
2. Open the web page.
3. Use `curl` or the API docs to POST a sample image and weight to `/measurement`.
4. If the model is not trained yet, choose the food manually from the dropdown.
5. Add it to the meal and verify the totals.

Example (replace `sample.jpg` with a real image):
```bash
curl -X POST http://127.0.0.1:8000/measurement \
  -F "weight_g=150" -F "image=@sample.jpg"
```

## 10. Nutrition calculation

For every nutrient:
`portion nutrient = (weight_g / 100) * nutrient_per_100g`

Example: 150 g × 130 kcal / 100 g = 195 kcal.

The app stores the food state and the values used for each meal item. This means changing a food's nutrition record later does not silently rewrite an old meal.

## 11. Suggested build order

1. Run backend and open the web app.
2. Import 5 foods into SQLite and verify `/foods`.
3. Test manual food selection and meal totals.
4. Test `/measurement` with a photo and simulated weight.
5. Collect images and train the classifier.
6. Put `best.pt` in `backend/models/` and test predictions.
7. Test Pi camera upload with simulated weight.
8. Implement HX711 driver, calibrate and test stable readings.
9. Integrate tare, stable-weight detection and full workflow.
10. Test on unseen foods/photos and document accuracy, weighing error, latency and estimated production cost.

## 12. Important prototype limitations

- A single camera image may not distinguish visually similar foods or preparation states. The app must allow user correction.
- A classifier recognises one main food per image. Mixed meals and overlapping foods are outside this MVP.
- Weight stability thresholds and calibration must be measured on your actual mechanical assembly.
- Nutrition data is approximate and depends on variety, preparation and database entry.
- Keep the backend on a trusted local network for the prototype. Add authentication before any wider deployment.
