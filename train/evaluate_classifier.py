import argparse
from ultralytics import YOLO

p = argparse.ArgumentParser()
p.add_argument("--weights", required=True)
p.add_argument("--data", required=True, help="Path to test/ directory with one subfolder per class")
args = p.parse_args()

model = YOLO(args.weights)
metrics = model.val(data=args.data, split="test")
print("Evaluation metrics:", metrics)
