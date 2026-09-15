"""Camera-only setup: capture frames, define regions and collect reference crops."""

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from workbench_common import ROOT

from .capture import open_capture
from .setup_tools import (
    annotated_frame,
    save_reference,
    set_region,
    validate_references,
    validate_regions,
)


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_new(path, data):
    """Refuse replacement of existing captures or working configurations."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Output already exists: {path}; choose a new filename")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def update_bench(path, bench):
    """Replace only the explicitly selected bench file after all validation succeeds."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(bench, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def camera_config(path):
    config = read_json(path).get("camera")
    if not isinstance(config, dict):
        raise ValueError("Runtime configuration needs a camera object")
    for key in ("width", "height"):
        if type(config.get(key)) is not int or config[key] <= 0:
            raise ValueError(f"Camera {key} must be a positive integer")
    if "device" not in config:
        raise ValueError("Camera device must be specified")
    return config


def load_frame(path, cv):
    frame = cv.imread(str(path))
    if frame is None:
        raise ValueError(f"Cannot read image: {path}")
    return frame


def png_bytes(frame, cv):
    ok, image = cv.imencode(".png", frame)
    if not ok:
        raise ValueError("PNG encoding failed")
    return image.tobytes()


def new_png_path(path):
    if path.suffix.lower() != ".png":
        raise ValueError("Output must use .png to preserve reference pixels")
    if path.exists():
        raise FileExistsError(f"Output already exists: {path}; choose a new filename")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Copy a bench configuration without opening a camera")
    init.add_argument("--source", type=Path, default=ROOT / "config/workbench/default.json")
    init.add_argument("--output", type=Path, required=True)

    capture = commands.add_parser(
        "capture", help="Open the selected REAL camera and save one raw frame; never open an arm"
    )
    capture.add_argument("--config", type=Path, default=ROOT / "config/development/pi.json")
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--discard-frames", type=int, default=5)

    for name, help_text in (
        ("overlay", "Save a region overlay for inspection; use raw images for references"),
        ("region", "Set one image region and clear its old references if the box changes"),
        ("reference", "Save a labeled region crop and update the working bench"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--bench", type=Path, required=True)
        command.add_argument("--image", type=Path, required=True)
        if name == "region":
            command.add_argument("--name", required=True)
            command.add_argument(
                "--box", type=int, nargs=4, metavar=("X", "Y", "W", "H"), required=True
            )
        else:
            command.add_argument("--output", type=Path, required=True)
        if name == "reference":
            command.add_argument("--region", required=True)
            command.add_argument("--label", required=True)

    check = commands.add_parser(
        "check", help="Validate configured regions and all reference images"
    )
    check.add_argument("--bench", type=Path, required=True)
    check.add_argument("--config", type=Path, default=ROOT / "config/development/pi.json")
    return parser


def run(args):
    if args.command == "init":
        bench = read_json(args.source)
        write_new(args.output, (json.dumps(bench, indent=2) + "\n").encode("utf-8"))
        return {"bench": str(args.output.resolve())}

    if args.command == "capture":
        # Reject mistakes before importing/opening a real device.
        new_png_path(args.output)
        if not 0 <= args.discard_frames <= 60:
            raise ValueError("--discard-frames must be between 0 and 60")
        camera = camera_config(args.config)
        import cv2

        capture = open_capture(camera, cv2)
        try:
            for _ in range(args.discard_frames):
                capture.read()
            frame = capture.read()
            received_at = datetime.now(timezone.utc).isoformat()
            if frame.shape != (camera["height"], camera["width"], 3):
                raise ValueError(
                    f"Camera returned shape {frame.shape}; expected "
                    f"({camera['height']}, {camera['width']}, 3). Adjust the camera configuration."
                )
            data = png_bytes(frame, cv2)
        finally:
            capture.close()
        write_new(args.output, data)
        return {
            "image": str(args.output.resolve()),
            "backend": camera.get("backend", "opencv"),
            "width": camera["width"],
            "height": camera["height"],
            "received_at": received_at,
        }

    bench = read_json(args.bench)
    import cv2

    if args.command == "check":
        camera = camera_config(args.config)
        errors = []
        try:
            validate_regions(bench, (camera["height"], camera["width"], 3))
        except ValueError as exc:
            errors.append(str(exc))
        errors.extend(validate_references(bench, cv2, root=ROOT))
        return {"ready": not errors, "errors": errors}

    if args.command == "reference":
        new_png_path(args.output)
        updated = save_reference(bench, args.image, args.region, args.label, args.output, cv2)
        try:
            update_bench(args.bench, updated)
        except OSError as exc:
            raise OSError(
                f"Reference saved at {args.output}, but bench update failed: {exc}"
            ) from exc
        return {"reference": str(args.output.resolve()), "bench": str(args.bench.resolve())}

    frame = load_frame(args.image, cv2)
    if args.command == "region":
        updated = set_region(bench, args.name, args.box, frame.shape)
        update_bench(args.bench, updated)
        return {"region": args.name, "box": args.box, "bench": str(args.bench.resolve())}

    new_png_path(args.output)
    overlay = annotated_frame(frame, bench, cv2)
    write_new(args.output, png_bytes(overlay, cv2))
    return {"overlay": str(args.output.resolve())}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 1 if result.get("ready") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
