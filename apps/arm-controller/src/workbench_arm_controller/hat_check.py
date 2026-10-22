"""Non-motion Waveshare/PCA9685 I2C diagnostics, independent of the arm service."""

import argparse
import errno
import json
from pathlib import Path


def check_hat(bus, address=0x40, *, register_test=False):
    """Inspect PCA9685-compatible registers; optionally test a disabled subaddress.

    PCA9685 has no chip ID. A successful check cannot identify the board vendor or
    establish PWM timing, servo power, wiring, or mechanical operation.
    """
    mode1 = bus.read_byte_data(address, 0x00)
    mode2 = bus.read_byte_data(address, 0x01)
    prescale = bus.read_byte_data(address, 0xFE)
    subaddress = bus.read_byte_data(address, 0x02)
    if mode2 & 0xE0 or prescale < 3 or subaddress & 1:
        raise ValueError("Register values are inconsistent with a PCA9685")
    # Single-byte reads work regardless of the current auto-increment setting.
    for channel in range(16):
        for offset in (1, 3):
            if bus.read_byte_data(address, 0x06 + channel * 4 + offset) & 0xE0:
                raise ValueError(f"Invalid reserved bits in channel {channel}")
    if register_test:
        if mode1 & 0x08:  # SUB1: never change an enabled group address.
            raise ValueError("SUBADR1 is enabled; refusing register test. Use read-only mode")
        try:
            for pattern in (0xAA, 0x54):
                bus.write_byte_data(address, 0x02, pattern)
                if bus.read_byte_data(address, 0x02) != pattern:
                    raise ValueError("SUBADR1 write/read verification failed")
        finally:
            # Attempt restoration even if a write took effect before raising an error.
            try:
                bus.write_byte_data(address, 0x02, subaddress)
                if bus.read_byte_data(address, 0x02) != subaddress:
                    raise ValueError("restored value did not match")
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "SUBADR1 restoration failed; inspect hardware before further use"
                ) from exc
    return {
        "status": "pass",
        "device": "PCA9685-compatible (board vendor unverified)",
        "address": f"0x{address:02x}",
        "mode1": f"0x{mode1:02x}",
        "mode2": f"0x{mode2:02x}",
        "prescale": prescale,
        "channels_read": 16,
        "register_test": "passed and restored" if register_test else "not requested",
        "limits": "PWM signals, servo power and arm movement are not verified",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default: 1)")
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x40)
    parser.add_argument(
        "--register-test",
        action="store_true",
        help="write/read/restore disabled SUBADR1; stop other I2C controllers first",
    )
    args = parser.parse_args(argv)
    if args.bus < 0:
        parser.error("--bus must be nonnegative")
    if not 0x40 <= args.address <= 0x77 or args.address == 0x70:
        parser.error("Use an individual PCA9685 address (0x40-0x77, excluding all-call 0x70)")
    device = Path(f"/dev/i2c-{args.bus}")
    result = {"status": "fail", "bus": str(device), "address": f"0x{args.address:02x}"}
    try:
        if not device.exists():
            raise FileNotFoundError(
                f"{device} is missing. Enable I2C using sudo raspi-config "
                "(Interface Options > I2C), reboot if requested, then retry. "
                "Use --bus only for the bus wired to the HAT."
            )
        from smbus2 import SMBus

        with SMBus(args.bus) as bus:
            result.update(check_hat(bus, args.address, register_test=args.register_test))
    except ImportError:
        result["error"] = "SMBus2 is missing. Run: uv sync --locked --extra pwm"
    except PermissionError:
        result["error"] = (
            f"No access to {device}; check device permissions and i2c group membership"
        )
    except OSError as exc:
        if exc.errno in (errno.ENXIO, errno.EREMOTEIO):
            result["error"] = (
                "No response at the selected address. Check HAT connection, logic power, "
                "address jumpers and bus selection."
            )
        else:
            result["error"] = str(exc)
    except ValueError as exc:
        result["error"] = str(exc)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
