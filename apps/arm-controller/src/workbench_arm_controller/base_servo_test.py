"""Manual channel-5 commissioning. Defaults to an offline plan; --live opens I2C.

No position/current feedback or stall detection. The first pulse can cause a large
movement. Pulse limits here are a restricted trial window, not measured joint limits.
"""

import argparse
import math
import select
import signal
import sys
import time

CHANNEL = 5
MIN_US = 1450
MAX_US = 1550
START_US = 1500
STEP_US = 10
PRESCALE = 121  # Approximately 50 Hz with the nominal 25 MHz internal oscillator.
TICK_US = (PRESCALE + 1) * 1_000_000 / 25_000_000


def encode_pulse(pulse_us):
    if not math.isfinite(pulse_us) or not MIN_US <= pulse_us <= MAX_US:
        raise ValueError(f"Requested pulse must be within {MIN_US}-{MAX_US} us")
    # Keep quantized output inside the trial window too.
    ticks = max(
        math.ceil(MIN_US / TICK_US), min(math.floor(MAX_US / TICK_US), round(pulse_us / TICK_US))
    )
    return ticks, ticks * TICK_US


def prepare(bus, address):
    """Disable every output before changing the shared PWM frequency."""
    if bus.read_byte_data(address, 0x00) & 0x40:
        raise ValueError("External clock enabled; refusing nominal internal-clock setup")
    bus.write_byte_data(address, 0xFD, 0x10)
    # ALL_LED full-off propagates to the individual channel registers.
    for channel in range(16):
        if not bus.read_byte_data(address, 0x09 + 4 * channel) & 0x10:
            raise ValueError(f"Could not verify channel {channel} full-off")
    bus.write_byte_data(address, 0x00, 0x30)  # Sleep + auto-increment, no group addresses.
    bus.write_byte_data(address, 0x01, 0x04)
    bus.write_byte_data(address, 0xFE, PRESCALE)
    bus.write_byte_data(address, 0x00, 0x20)
    time.sleep(0.005)
    if (
        bus.read_byte_data(address, 0x00) & 0x7F != 0x20
        or bus.read_byte_data(address, 0x01) != 0x04
        or bus.read_byte_data(address, 0xFE) != PRESCALE
    ):
        raise ValueError("PWM configuration readback failed")


def write_pulse(bus, address, pulse_us):
    ticks, actual_us = encode_pulse(pulse_us)
    register = 0x06 + 4 * CHANNEL
    data = [0, 0, ticks & 0xFF, ticks >> 8]
    bus.write_i2c_block_data(address, register, data)
    for offset, expected in enumerate(data):
        if bus.read_byte_data(address, register + offset) != expected:
            raise ValueError("Channel 5 pulse register readback failed")
    print(f"Channel 5: requested {pulse_us} us; nominal encoded {actual_us:.2f} us", flush=True)


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


def run_live(bus_number, address):
    from smbus2 import SMBus

    print(
        "Servo power must be OFF. Disconnect power to the other five servos.\n"
        "Secure the base, support the arm, clear its rotation path and cables.\n"
        "Keep the physical servo-power cutoff within reach. Stop other HAT users.\n"
        "The first 1500 us pulse may cause a large movement; there is no stall detection.\n"
        "This command disables ALL outputs and changes the shared frequency."
    )
    if input("Type PREPARE with servo power OFF: ").strip() != "PREPARE":
        print("Cancelled before opening I2C.")
        return 0
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with SMBus(bus_number) as bus:
            try:
                prepare(bus, address)
                deadline = time.monotonic() + 60
                print("Outputs are off. Turn on ONLY base-servo power (approximately 5 V).")
                if (
                    read_command("Type START to send 1500 us, or anything else to quit: ", deadline)
                    != "START"
                ):
                    return 0
                pulse_us = START_US
                write_pulse(bus, address, pulse_us)
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
                    write_pulse(bus, address, target)
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
    parser.add_argument("--bus", type=int, default=1)
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x40)
    args = parser.parse_args(argv)
    if args.bus < 0 or not 0x40 <= args.address <= 0x77 or args.address == 0x70:
        parser.error("Invalid bus or individual PCA9685 address")
    print(
        f"Channel {CHANNEL}; start {START_US} us; window {MIN_US}-{MAX_US} us; "
        f"step {STEP_US} us; nominal frequency {1_000_000 / (4096 * TICK_US):.2f} Hz."
    )
    if not args.live:
        print("Offline plan only. No I2C access. --live enables the supervised terminal test.")
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("--live requires an interactive terminal; do not pipe input")
    try:
        return run_live(args.bus, args.address)
    except (KeyboardInterrupt, EOFError, TimeoutError):
        print("Test stopped. Turn servo power OFF.", file=sys.stderr)
        return 1
    except (ImportError, OSError, ValueError) as exc:
        print(
            f"Test failed: {exc}. Turn servo power OFF; inspect before retrying.", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
