import argparse
from ultralytics import YOLO

p = argparse.ArgumentParser()
p.add_argument("--data", default="datasets/addgram_foods")
p.add_argument("--epochs", type=int, default=50)
p.add_argument("--imgsz", type=int, default=224)
p.add_argument("--batch", type=int, default=16)
args = p.parse_args()

model = YOLO("yolo11n-cls.pt")
model.train(data=args.data, epochs=args.epochs, imgsz=args.imgsz,
            batch=args.batch, project="runs/classify", name="addgram")
