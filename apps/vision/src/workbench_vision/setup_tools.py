"""Offline camera-region setup and reference-image checks, without arm access."""

from copy import deepcopy
from pathlib import Path


def _region_names(bench):
    return {"tray", *(tool["id"] for tool in bench["tools"])}


def _box(region, box, frame_shape=None):
    if (
        not isinstance(box, (list, tuple))
        or len(box) != 4
        or any(type(value) is not int for value in box)
    ):
        raise ValueError(f"Region {region} must be four integers [x, y, width, height]")
    x, y, width, height = box
    if min(x, y) < 0 or min(width, height) <= 0:
        raise ValueError(f"Region {region} needs nonnegative x/y and positive width/height")
    if frame_shape is not None:
        if len(frame_shape) < 2 or min(frame_shape[:2]) <= 0:
            raise ValueError("Camera frame must have positive height and width")
        if x + width > frame_shape[1] or y + height > frame_shape[0]:
            raise ValueError(f"Region {region} lies outside camera frame")
    return x, y, width, height


def validate_regions(bench, frame_shape):
    """Require exactly one in-bounds region for each tool and the tray."""
    regions = bench["vision"]["regions"]
    expected = _region_names(bench)
    if set(regions) != expected:
        missing, extra = sorted(expected - set(regions)), sorted(set(regions) - expected)
        raise ValueError(
            f"Region names do not match tools and tray; missing={missing}, extra={extra}"
        )
    for region, box in regions.items():
        _box(region, box, frame_shape)


def annotated_frame(frame, bench, cv):
    """Draw named region borders on a copy; preserve the unannotated source."""
    validate_regions(bench, frame.shape)
    result = frame.copy()
    for region, (x, y, width, height) in bench["vision"]["regions"].items():
        cv.rectangle(result, (x, y), (x + width - 1, y + height - 1), (0, 255, 0), 2)
        cv.putText(
            result, region, (x, max(12, y - 5)), cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1
        )
    return result


def set_region(bench, region, box, frame_shape):
    """Return an updated copy; other regions may still await calibration."""
    if region not in _region_names(bench):
        raise ValueError(f"Unknown region: {region}")
    _box(region, box, frame_shape)
    result = deepcopy(bench)
    if result["vision"]["regions"].get(region) != list(box):
        result["vision"].get("references", {}).pop(region, None)
    result["vision"]["regions"][region] = list(box)
    return result


def save_reference(bench, image_path, region, label, output_path, cv):
    """Crop a saved BGR frame to a new PNG and return updated reference paths."""
    if region not in _region_names(bench):
        raise ValueError(f"Unknown region: {region}")
    labels = {"empty", *(tool["id"] for tool in bench["tools"])}
    if label not in labels:
        raise ValueError(f"Unknown reference label: {label}")
    output = Path(output_path).resolve()
    if output.suffix.lower() != ".png":
        raise ValueError("Reference output must use the lossless .png extension")
    image = cv.imread(str(image_path), cv.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read source image: {image_path}")
    x, y, width, height = _box(region, bench["vision"]["regions"][region], image.shape)
    ok, encoded = cv.imencode(".png", image[y : y + height, x : x + width])
    if not ok:
        raise ValueError("Reference PNG encoding failed")
    result = deepcopy(bench)
    result["vision"].setdefault("references", {}).setdefault(region, {})[label] = str(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as destination:
        destination.write(encoded.tobytes())
    return result


def validate_references(bench, cv, root=None):
    """Return reference matrix/readability/size problems without classification."""
    errors = []
    regions = bench["vision"]["regions"]
    expected = _region_names(bench)
    labels = {"empty", *(tool["id"] for tool in bench["tools"])}
    references = bench["vision"].get("references", {})
    if not isinstance(references, dict):
        return ["References must be a mapping of region names to label/image paths"]
    for region in sorted(set(regions) - expected):
        errors.append(f"Unknown region: {region}")
    for region in sorted(set(references) - expected):
        errors.append(f"Unknown reference region: {region}")
    for region in sorted(expected):
        if region not in regions:
            errors.append(f"Missing region: {region}")
            continue
        try:
            _, _, width, height = _box(region, regions[region])
        except ValueError as exc:
            errors.append(str(exc))
            continue
        paths = references.get(region, {})
        if not isinstance(paths, dict):
            errors.append(f"Region {region}: references must map labels to image paths")
            continue
        for label in sorted(set(paths) - labels):
            errors.append(f"Region {region}: unknown reference label {label}")
        for label in sorted(labels):
            if label not in paths:
                errors.append(f"Region {region}: missing reference for {label}")
                continue
            raw_path = paths[label]
            if not isinstance(raw_path, str) or not raw_path.strip():
                errors.append(f"Region {region}/{label}: reference path must be a nonempty string")
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = Path(root or Path.cwd()) / path
            if not path.is_file():
                errors.append(f"Region {region}/{label}: missing reference image {path}")
                continue
            image = cv.imread(str(path), cv.IMREAD_COLOR)
            if image is None:
                errors.append(f"Region {region}/{label}: cannot read reference image {path}")
            elif image.shape[:2] != (height, width):
                errors.append(
                    f"Region {region}/{label}: reference size {image.shape[1]}x{image.shape[0]} "
                    f"does not match region {width}x{height}"
                )
    return errors
