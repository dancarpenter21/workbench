"""Supervised claw neutral/current test. Offline by default; channel 0 only."""

import argparse
import math
import select
import signal
import sys
import time

from .base_servo_test import interrupted
from .claw_current import check_current, read_sample
from .device_lock import DeviceLock
from .neutral_servo_test import disable_outputs
from .servo_trial import prepare, write_pulse


def run_live(args):
    from smbus2 import SMBus

    print(
        "Turn ALL servo power OFF. Isolate power to servos 1–5, support the arm,\n"
        "clear the claw and keep the physical cutoff ready. Stop other HAT users.\n"
        "Preparation disables all outputs; powering the claw may cause a large movement.\n"
        "Exit/timeout/fault disables PWM, which does not disconnect servo power."
    )
    if input("Type PREPARE with ALL servo power OFF: ").strip() != "PREPARE":
        return 0
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        with DeviceLock(args.bus, args.address), SMBus(args.bus) as bus:
            try:
                sample = read_sample(bus, args.sensor_address, args.shunt_ohms)
                if sample["bus_v"] > 0.5:
                    raise ValueError("Claw supply still present; turn power OFF")
                prepare(bus, args.address)
                actual = write_pulse(bus, args.address, 1500, 0)
                print(f"Claw/channel 0: 1500 us requested, {actual:.2f} us nominal encoded.")
                print("Apply power ONLY to the claw. Monitoring for 60 seconds; q + Enter stops.")
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    sample = read_sample(bus, args.sensor_address, args.shunt_ohms)
                    current = sample["current_a"]
                    current_text = (
                        "current unknown (shunt resistance unset)"
                        if current is None
                        else f"current={current:.4f} A, abs={abs(current):.4f} A"
                    )
                    print(
                        f"bus={sample['bus_v']:.3f} V "
                        f"shunt={sample['shunt_mv']:.3f} mV; {current_text}",
                        flush=True,
                    )
                    check_current(sample, args.limit_a)
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        line = sys.stdin.readline()
                        if not line or line.strip().lower() == "q":
                            break
            finally:
                disable_outputs(bus, args.address)
                print("All outputs verified full-off. Turn physical servo power OFF.")
    finally:
        signal.signal(signal.SIGTERM, previous)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--bus", type=int, default=1)
    parser.add_argument("--address", type=lambda v: int(v, 0), default=0x40)
    parser.add_argument("--sensor-address", type=lambda v: int(v, 0), default=0x41)
    parser.add_argument("--shunt-ohms", type=float, help="verified shunt resistance in ohms")
    parser.add_argument("--limit-a", type=float, default=2.5)
    args = parser.parse_args(argv)
    if args.bus < 0 or not 0x40 <= args.address <= 0x77 or args.address == 0x70:
        parser.error("Invalid bus or individual PCA9685 address")
    if not 0x40 <= args.sensor_address <= 0x4F or args.sensor_address == args.address:
        parser.error("INA219 address must be distinct and within 0x40–0x4f")
    for name, value in (("--shunt-ohms", args.shunt_ohms), ("--limit-a", args.limit_a)):
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(f"{name} must be finite and positive")
    if args.limit_a > 2.5:
        parser.error("--limit-a cannot exceed the mandatory 2.5 A cutoff")
    print("Claw/channel 0: 1500 us; INA219 monitoring; no jogging or sweep.")
    if args.shunt_ohms is None:
        print("Raw voltage monitoring only. Ampere values and 2.5 A protection are unverified.")
    else:
        print(f"Shunt {args.shunt_ohms:g} ohm; disable PWM at abs(current) >= {args.limit_a:g} A.")
    print("Polling can miss short peaks; PWM shutdown is not a physical power cutoff.")
    if not args.live:
        print("Offline plan only. No I2C access. Use --live for the supervised test.")
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("--live requires an interactive terminal; do not pipe input")
    try:
        return run_live(args)
    except (KeyboardInterrupt, EOFError, ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"Test stopped: {exc}. Turn physical servo power OFF.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
