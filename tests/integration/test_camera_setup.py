from copy import deepcopy
from unittest.mock import MagicMock

import pytest
from workbench_vision.setup_tools import (
    annotated_frame,
    save_reference,
    set_region,
    validate_references,
    validate_regions,
)


@pytest.fixture
def bench():
    return {
        "tools": [{"id": "pliers"}, {"id": "driver"}],
        "vision": {
            "regions": {"pliers": [2, 3, 8, 7], "driver": [12, 3, 6, 7], "tray": [2, 12, 20, 6]},
            "references": {},
            "match_threshold": 0.94,
        },
        "arm": {"calibrated": False, "sequences": {"pliers": [[1, 2, 3, 4]]}},
        "custom": {"keep": [1, 2]},
    }


@pytest.fixture
def cv():
    return pytest.importorskip("cv2")


@pytest.fixture
def image(cv, tmp_path):
    np = pytest.importorskip("numpy")
    frame = np.arange(20 * 30 * 3, dtype=np.uint8).reshape((20, 30, 3))
    path = tmp_path / "source.png"
    assert cv.imwrite(str(path), frame)
    return path, frame


def test_region_validation_requires_exact_names(bench):
    validate_regions(bench, (20, 30, 3))
    bench["vision"]["regions"]["extra"] = bench["vision"]["regions"].pop("tray")
    with pytest.raises(ValueError, match="missing=\\['tray'\\], extra=\\['extra'\\]"):
        validate_regions(bench, (20, 30, 3))


@pytest.mark.parametrize(
    "box",
    [
        [1, 2, 3],
        [True, 2, 3, 4],
        [1.0, 2, 3, 4],
        ["1", 2, 3, 4],
        [-1, 2, 3, 4],
        [1, -1, 3, 4],
        [1, 2, 0, 4],
        [1, 2, 3, -1],
        [29, 2, 2, 4],
        [1, 19, 3, 2],
    ],
)
def test_region_validation_rejects_malformed_or_out_of_bounds_box(bench, box):
    bench["vision"]["regions"]["pliers"] = box
    with pytest.raises(ValueError, match="Region pliers"):
        validate_regions(bench, (20, 30, 3))


def test_region_on_frame_boundary_is_valid(bench):
    bench["vision"]["regions"]["pliers"] = [0, 0, 30, 20]
    validate_regions(bench, (20, 30, 3))


def test_changing_one_region_preserves_other_configuration_and_invalidates_its_crops(bench):
    bench["vision"]["regions"]["driver"] = [200, 300, 60, 70]
    bench["vision"]["references"] = {
        "pliers": {"empty": "old-crop.png"},
        "driver": {"empty": "keep.png"},
    }
    original = deepcopy(bench)
    result = set_region(bench, "pliers", [1, 2, 3, 4], (20, 30, 3))
    assert bench == original
    assert result["vision"]["regions"]["pliers"] == [1, 2, 3, 4]
    assert result["vision"]["references"] == {"driver": {"empty": "keep.png"}}
    assert result["vision"]["regions"]["driver"] == [200, 300, 60, 70]
    assert result["arm"] == bench["arm"]
    result["custom"]["keep"].append(3)
    assert bench["custom"]["keep"] == [1, 2]


def test_unchanged_region_retains_references(bench):
    bench["vision"]["references"] = {"pliers": {"empty": "keep.png"}}
    result = set_region(bench, "pliers", (2, 3, 8, 7), (20, 30, 3))
    assert result == bench


def test_unknown_region_is_rejected_without_mutation(bench):
    original = deepcopy(bench)
    with pytest.raises(ValueError, match="Unknown region"):
        set_region(bench, "arm", [1, 2, 3, 4], (20, 30, 3))
    assert bench == original


def test_annotations_do_not_modify_source_or_configuration(bench, cv, image):
    np = pytest.importorskip("numpy")
    _, frame = image
    original_frame, original_bench = frame.copy(), deepcopy(bench)
    result = annotated_frame(frame, bench, cv)
    assert np.array_equal(frame, original_frame)
    assert not np.array_equal(result, original_frame)
    assert bench == original_bench


def test_reference_saved_as_lossless_crop_with_absolute_path_and_no_overwrite(
    bench, cv, image, tmp_path
):
    np = pytest.importorskip("numpy")
    source, frame = image
    original = deepcopy(bench)
    output = tmp_path / "references" / "pliers-empty.png"
    result = save_reference(bench, source, "pliers", "empty", output, cv)
    assert bench == original
    assert result["vision"]["references"]["pliers"]["empty"] == str(output.resolve())
    assert result["arm"] == bench["arm"]
    assert np.array_equal(cv.imread(str(output)), frame[3:10, 2:10])
    saved_bytes = output.read_bytes()
    with pytest.raises(FileExistsError):
        save_reference(bench, source, "pliers", "empty", output, cv)
    assert output.read_bytes() == saved_bytes


@pytest.mark.parametrize("region,label", [("unknown", "empty"), ("pliers", "unknown")])
def test_reference_rejects_unknown_region_or_label(bench, image, cv, tmp_path, region, label):
    output = tmp_path / "new-directory" / "crop.png"
    with pytest.raises(ValueError, match="Unknown"):
        save_reference(bench, image[0], region, label, output, cv)
    assert not output.parent.exists()


def test_reference_validates_source_crop_before_creating_output(bench, cv, image, tmp_path):
    bench["vision"]["regions"]["pliers"] = [29, 19, 8, 7]
    output = tmp_path / "new-directory" / "crop.png"
    with pytest.raises(ValueError, match="outside camera frame"):
        save_reference(bench, image[0], "pliers", "empty", output, cv)
    assert not output.parent.exists()


def test_reference_rejects_unreadable_image_and_lossy_extension(bench, cv, image, tmp_path):
    with pytest.raises(ValueError, match="Cannot read source image"):
        save_reference(bench, tmp_path / "missing.png", "pliers", "empty", tmp_path / "a.png", cv)
    with pytest.raises(ValueError, match="lossless .png"):
        save_reference(bench, image[0], "pliers", "empty", tmp_path / "a.jpg", cv)


def test_reference_encoder_failure_writes_nothing(bench, image, tmp_path):
    cv = MagicMock()
    cv.imread.return_value = image[1]
    cv.imencode.return_value = False, None
    output = tmp_path / "new-directory" / "crop.png"
    with pytest.raises(ValueError, match="encoding failed"):
        save_reference(bench, image[0], "pliers", "empty", output, cv)
    assert not output.parent.exists()


def test_reference_validation_reports_every_missing_label(bench, cv):
    errors = validate_references(bench, cv)
    assert len(errors) == 9
    assert "Region tray: missing reference for empty" in errors
    assert "Region pliers: missing reference for driver" in errors


def test_complete_reference_matrix_resolves_relative_paths_and_checks_sizes(
    bench, cv, image, tmp_path
):
    source, _ = image
    for region in bench["vision"]["regions"]:
        for label in ("empty", "pliers", "driver"):
            output = tmp_path / f"{region}-{label}.png"
            bench = save_reference(bench, source, region, label, output, cv)
            bench["vision"]["references"][region][label] = output.name
    assert validate_references(bench, cv, root=tmp_path) == []
    # Reuse a different-size crop: the image remains readable but is stale for this region.
    bench["vision"]["references"]["tray"]["empty"] = "pliers-empty.png"
    assert validate_references(bench, cv, root=tmp_path) == [
        "Region tray/empty: reference size 8x7 does not match region 20x6"
    ]


def test_reference_validation_distinguishes_missing_unreadable_and_unknown(bench, cv, tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_text("not an image", encoding="utf-8")
    bench["vision"]["references"] = {
        "pliers": {"empty": "missing.png", "pliers": broken.name, "driver": "", "other": "x"},
        "unknown": {},
    }
    errors = validate_references(bench, cv, root=tmp_path)
    assert "Unknown reference region: unknown" in errors
    assert "Region pliers: unknown reference label other" in errors
    assert any("pliers/empty: missing reference image" in error for error in errors)
    assert any("pliers/pliers: cannot read reference image" in error for error in errors)
    assert any(
        "pliers/driver: reference path must be a nonempty string" in error for error in errors
    )
