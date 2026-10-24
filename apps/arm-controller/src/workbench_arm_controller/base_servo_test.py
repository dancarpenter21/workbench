"""Manual channel-4/5 commissioning. Defaults to an offline plan; --live opens I2C.

No position/current feedback or stall detection. The first pulse can cause a large
movement. Pulse limits here are a restricted trial window, not measured joint limits.
"""

import argparse
import select
import signal
import sys
import time

from .device_lock import DeviceLock
from .servo_trial import MAX_US, MIN_US, START_US, STEP_US, TICK_US, prepare
from .servo_trial import encode_pulse as encode_pulse
from .servo_trial import write_pulse as write_trial_pulse

CHANNEL = 5


def write_pulse(bus, address, pulse_us, channel=CHANNEL):
    actual_us = write_trial_pulse(bus, address, pulse_us, channel)
    print(
        f"Channel {channel}: requested {pulse_us} us; nominal encoded {actual_us:.2f} us",
        flush=True,
    )


def read_command(prompt, deadline):
    """Stop on 15 seconds of inactivity or the 60-second session deadline."""
    print(prompt, end="", flush=True)
    remaining = min(15, deadline - time.monotonic())
    if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
        raise TimeoutError("Input/session timeout")
    line = sys.stdin.readline()
    if not line:
        raise EOFError("Terminal input closed")
    return line.strip()


def interrupted(signum, frame):
    raise KeyboardInterrupt


def run_live(bus_number, address, channel=CHANNEL):
    from smbus2 import SMBus

    print(
        "Servo power must be OFF. Disconnect power to the other five servos.\n"
        "Secure the base, support every arm link, clear the joint's path and cables.\n"
        "Keep the physical servo-power cutoff within reach. Stop other HAT users.\n"
        "The first 1500 us pulse may cause a large movement; there is no stall detection.\n"
        "This command disables ALL outputs and changes the shared frequency."
    )
    if input("Type PREPARE with servo power OFF: ").strip() != "PREPARE":
        print("Cancelled before opening I2C.")
        return 0
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with DeviceLock(bus_number, address), SMBus(bus_number) as bus:
            try:
                prepare(bus, address)
                deadline = time.monotonic() + 60
                print(f"Outputs are off. Turn on ONLY servo {channel} power (approximately 5 V).")
                if (
                    read_command("Type START to send 1500 us, or anything else to quit: ", deadline)
                    != "START"
                ):
                    return 0
                pulse_us = START_US
                write_pulse(bus, address, pulse_us, channel)
                while True:
                    command = read_command(
                        "+ / - then Enter: 10 us step; q then Enter: off > ", deadline
                    )
                    if command == "q":
                        break
                    if command not in {"+", "-"}:
                        print("Use +, -, or q. Cut physical power immediately if binding occurs.")
                        continue
                    target = pulse_us + (STEP_US if command == "+" else -STEP_US)
                    if not MIN_US <= target <= MAX_US:
                        print("Trial-window limit reached; no command sent.")
                        continue
                    write_pulse(bus, address, target, channel)
                    pulse_us = target
            finally:
                # Never restore old pulses or command a return-to-center movement.
                try:
                    bus.write_byte_data(address, 0xFD, 0x10)
                    for channel in range(16):
                        if not bus.read_byte_data(address, 0x09 + 4 * channel) & 0x10:
                            raise OSError("Full-off readback failed")
                    print("All output registers verified full-off. Turn servo power OFF.")
                except OSError as exc:
                    raise OSError("Could not confirm outputs off. CUT SERVO POWER NOW.") from exc
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="enable interactive hardware access")
    parser.add_argument(
        "--channel",
        type=int,
        choices=(4, 5),
        default=CHANNEL,
        help="zero-based servo channel (default: 5/base); 4 tests the next joint",
    )
    parser.add_argument("--bus", type=int, default=1)
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x40)
    args = parser.parse_args(argv)
    if args.bus < 0 or not 0x40 <= args.address <= 0x77 or args.address == 0x70:
        parser.error("Invalid bus or individual PCA9685 address")
    print(
        f"Channel {args.channel}; start {START_US} us; window {MIN_US}-{MAX_US} us; "
        f"step {STEP_US} us; nominal frequency {1_000_000 / (4096 * TICK_US):.2f} Hz."
    )
    if not args.live:
        print("Offline plan only. No I2C access. --live enables the supervised terminal test.")
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("--live requires an interactive terminal; do not pipe input")
    try:
        return run_live(args.bus, args.address, args.channel)
    except (KeyboardInterrupt, EOFError, TimeoutError):
        print("Test stopped. Turn servo power OFF.", file=sys.stderr)
        return 1
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        print(
            f"Test failed: {exc}. Turn servo power OFF; inspect before retrying.", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
