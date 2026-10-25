"""Deliberately synthetic geometry; not a physical ROB0036 calibration."""

from workbench_arm_controller.movement import ArmGeometry, MovementSession


def geometry_data(domain="simulated"):
    origins = ((0, 0, 100), (100, 0, 100), (160, 0, 100), (200, 0, 100))
    return {
        "schema_version": 1,
        "revision": "synthetic-test-only",
        "domain": domain,
        "calibrated": True,
        "reference_pulse_us": 1500,
        "base_axis_perpendicular_to_table": True,
        "claw_envelope": "full_opening_range",
        "clearance_mm": 10,
        "geometry_error_mm": 1,
        "joints": [
            {
                "channel": channel,
                "origin_mm": list(origin),
                "axis": [0, -1, 0],
                "pulse_angle_deg": [[500, -90], [1500, 0], [2500, 90]],
                "error_deg": 0.5,
            }
            for channel, origin in zip((4, 3, 2, 1), origins, strict=True)
        ],
        "capsules": [
            {
                "name": f"link_{channel}",
                "parent_channel": channel,
                "a_mm": list(origins[index]),
                "b_mm": list(origins[index + 1] if index < 3 else (220, 0, 100)),
                "radius_mm": 5,
            }
            for index, channel in enumerate((4, 3, 2, 1))
        ]
        + [
            {
                "name": "claw",
                "parent_channel": 1,
                "a_mm": [220, 0, 100],
                "b_mm": [240, 0, 100],
                "radius_mm": 10,
            }
        ],
    }


def movement_session():
    return MovementSession(ArmGeometry.from_dict(geometry_data()))
