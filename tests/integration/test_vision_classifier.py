import pytest
from workbench_common import settings
from workbench_vision.app import Camera

np = pytest.importorskip("numpy", reason="Install the vision extra to test image classification")
cv = pytest.importorskip("cv2", reason="Install the vision extra to test image classification")


def classifier():
    camera = Camera.__new__(Camera)
    camera.cv = cv
    camera.cfg = settings()
    camera.references = {
        "source": {
            "empty": np.full((64, 64, 3), 240, dtype="float32"),
            "pliers": np.full((64, 64, 3), (20, 100, 180), dtype="float32"),
            "screwdriver": np.full((64, 64, 3), (150, 20, 80), dtype="float32"),
        }
    }
    return camera


def test_known_and_wrong_tool_images_are_distinguished():
    camera = classifier()
    for label in ["empty", "pliers", "screwdriver"]:
        image = camera.references["source"][label].astype("uint8")
        detected, confidence = camera.classify(image, "source")
        assert detected == label
        assert confidence == 1


def test_unknown_image_and_ambiguous_references_rejected():
    camera = classifier()
    assert camera.classify(np.zeros((64, 64, 3), dtype="uint8"), "source")[0] == "uncertain"
    camera.references["source"]["screwdriver"] = camera.references["source"]["pliers"].copy()
    assert camera.classify(camera.references["source"]["pliers"], "source")[0] == "uncertain"
