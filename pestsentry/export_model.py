"""Export trusted YOLOv8 detection weights on a laptop and prepare a hub config."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("weights", type=Path)
    parser.add_argument("--classes", nargs="+", default=["Mouse", "Raccoon", "Squirrel"])
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.example.json"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("config.local.json"))
    args = parser.parse_args()
    if not args.weights.is_file() or args.output.exists() or args.weights.with_suffix(".onnx").exists():
        parser.error("Supply a local .pt checkpoint and unused .onnx and config output paths.")
    from ultralytics import YOLO

    config = json.loads(args.config.read_text(encoding="utf-8"))
    model = YOLO(str(args.weights))
    if model.task != "detect":
        parser.error("The checkpoint must be a detection model.")
    labels = [model.names[i] for i in range(len(model.names))]
    by_name = {label.casefold(): label for label in labels}
    missing = [label for label in args.classes if label.casefold() not in by_name]
    if missing:
        parser.error(f"Classes missing from this checkpoint: {missing}")
    exported = model.export(format="onnx", imgsz=config["model"]["input_size"], batch=1,
                            dynamic=False, half=False, nms=False, simplify=False, opset=12, device="cpu")
    config["model"].update(backend="onnx", path=Path(os.path.relpath(exported, args.output.parent)).as_posix(), labels=labels)
    config["alerts"]["classes"] = [by_name[name.casefold()] for name in args.classes]
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(config, stream, indent=2)
        stream.write("\n")
    print(f"Exported {exported} and wrote {args.output}")


if __name__ == "__main__":
    main()
