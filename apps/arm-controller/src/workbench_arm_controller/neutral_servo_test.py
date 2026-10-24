"""Supervised neutral test; servos 1–5 by default, all six with --include-claw.

No measured position feedback. Register writes are sequential, not synchronized motion.
"""

import argparse
import select
import signal
import sys
import time

from .base_servo_test import interrupted, read_command, write_pulse
from .device_lock import DeviceLock
from .servo_trial import START_US, prepare

CHANNELS = (5, 4, 3, 2, 1)


def disable_outputs(bus, address):
    bus.write_byte_data(address, 0xFD, 0x10)
    for channel in range(16):
        if not bus.read_byte_data(address, 0x09 + 4 * channel) & 0x10:
            raise OSError("Could not confirm outputs off. CUT SERVO POWER NOW.")


def prepare_neutral(bus, address, *, include_claw=False):
    """Call with all servo power disconnected; program the selected channels at midpoint."""
    try:
        prepare(bus, address)
        for channel in (*CHANNELS, 0) if include_claw else CHANNELS:
            write_pulse(bus, address, START_US, channel)
    except BaseException:
        # A partial write must not leave a subset of joints enabled.
        disable_outputs(bus, address)
        raise


def run_live(bus_number, address, *, include_claw=False):
    from smbus2 import SMBus

    print(
        (
            "Servo power must be OFF. All six servos selected.\n"
            if include_claw
            else "Servo power must be OFF. Keep claw/servo 0 physically disconnected from power.\n"
        )
        + "Secure the base, support every link, and clear the entire motion path.\n"
        "Keep the physical cutoff within reach. Stop other HAT users.\n"
        "Selected servos will receive 1500 us; applying power may move them immediately.\n"
        "This disables ALL outputs and changes the shared frequency.\n"
        "There is no position feedback, speed control, or stall detection.\n"
        "On exit or timeout all outputs turn off: support the arm against collapse."
    )
    if input("Type PREPARE with ALL servo power OFF: ").strip() != "PREPARE":
        print("Cancelled before opening I2C.")
        return 0
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with DeviceLock(bus_number, address), SMBus(bus_number) as bus:
            try:
                if include_claw:
                    from .claw_servo_test import read_sample

                    sample = read_sample(bus, 0x41)
                    if sample["bus_v"] > 0.5:
                        raise ValueError("Claw supply still present; turn power OFF")
                    print("INA219 raw monitoring active; amperes/2.5 A cutoff unverified.")
                prepare_neutral(bus, address, include_claw=include_claw)
                deadline = time.monotonic() + 60
                print(
                    (
                        "Channels 0–5 are programmed at 1500 us.\n"
                        if include_claw
                        else "Only channels 1–5 are programmed at 1500 us; claw/0 remains disabled.\n"
                    )
                    + "While supporting the links, apply power ONLY to the selected servos.\n"
                    "Observe the combined neutral pose; cut power immediately for binding.\n"
                    "15 seconds without input or 60 seconds total disables all outputs."
                )
                last_input = time.monotonic()
                while True:
                    if include_claw:
                        sample = read_sample(bus, 0x41)
                        print(
                            f"Claw bus={sample['bus_v']:.3f} V; "
                            f"shunt={sample['shunt_mv']:.3f} mV; current unknown",
                            flush=True,
                        )
                        if time.monotonic() >= min(deadline, last_input + 15):
                            raise TimeoutError("Input/session timeout")
                        if not select.select([sys.stdin], [], [], 0.1)[0]:
                            continue
                        line = sys.stdin.readline()
                        if not line:
                            raise EOFError("Terminal input closed")
                        last_input = time.monotonic()
                        if line.strip() == "q":
                            break
                        continue
                    command = read_command(
                        "Enter: continue observing; q then Enter: disable outputs > ", deadline
                    )
                    if command == "q":
                        break
            finally:
                disable_outputs(bus, address)
                print("All output registers verified full-off. Turn servo power OFF.")
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-claw", action="store_true", help="select all six servos")
    parser.add_argument("--live", action="store_true", help="enable interactive hardware access")
    parser.add_argument("--bus", type=int, default=1)
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x40)
    args = parser.parse_args(argv)
    if args.bus < 0 or not 0x40 <= args.address <= 0x77 or args.address == 0x70:
        parser.error("Invalid bus or individual PCA9685 address")
    print(
        "Channels 0–5: 1500 us midpoint each."
        if args.include_claw
        else "Channels 1–5: 1500 us midpoint each. Claw/channel 0 remains disabled and unpowered."
    )
    if not args.live:
        print("Offline plan only. No I2C access. --live enables the supervised terminal test.")
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("--live requires an interactive terminal; do not pipe input")
    try:
        return run_live(args.bus, args.address, include_claw=args.include_claw)
    except (KeyboardInterrupt, EOFError, TimeoutError):
        print("Test stopped. Support the arm and turn servo power OFF.", file=sys.stderr)
        return 1
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"Test failed: {exc}. CUT SERVO POWER; inspect before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
