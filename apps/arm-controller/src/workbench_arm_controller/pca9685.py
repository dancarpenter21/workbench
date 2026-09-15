"""ROB0036 pulse-output primitives and an offline pose checker.

This is not a retrieval driver. PWM commands do not measure joint position.
The hardware class is for future supervised commissioning; the CLI never opens I2C.
"""

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

AUTOMATION_UNAVAILABLE = (
    "PCA9685 automatic retrieval is disabled: starting-pose confirmation, "
    "motion completion and stop/recovery without measured joint feedback "
    "have not been commissioned. Use mock mode or the offline pulse checker."
)


@dataclass(frozen=True)
class Pulse:
    joint: str
    channel: int
    requested_us: float
    commanded_us: float
    ticks: int


class PulsePlan:
    """Validate all six targets before generating any device writes."""

    def __init__(self, calibration, reference_clock_hz=25_000_000):
        if calibration.get("calibrated") is not True:
            raise ValueError("Pulse output disabled: measured calibration is required")
        if (
            isinstance(reference_clock_hz, bool)
            or not isinstance(reference_clock_hz, (int, float))
            or not math.isfinite(reference_clock_hz)
            or not 10_000_000 <= reference_clock_hz <= 50_000_000
        ):
            raise ValueError("Invalid PCA9685 reference clock")
        # 50 Hz is the initial commissioning frequency; verify on the actual servos.
        self.prescale = round(reference_clock_hz / (4096 * 50)) - 1
        self.tick_us = (self.prescale + 1) * 1_000_000 / reference_clock_hz
        channels = calibration.get("channels")
        if not isinstance(channels, dict) or len(channels) != 6:
            raise ValueError("Provide exactly six measured joint/channel mappings")
        self.channels = {}
        seen = set()
        for joint, data in channels.items():
            if not isinstance(joint, str) or not joint.strip() or not isinstance(data, dict):
                raise ValueError("Invalid joint calibration")
            channel = data.get("channel")
            low, high = data.get("min_us"), data.get("max_us")
            if type(channel) is not int or not 0 <= channel <= 15 or channel in seen:
                raise ValueError("Channels must be unique integers from 0 to 15")
            if (
                any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                    for v in (low, high)
                )
                or not 500 <= low < high <= 2500
            ):
                raise ValueError("Measured pulse limits must lie within 500-2500 microseconds")
            minimum, maximum = math.ceil(low / self.tick_us), math.floor(high / self.tick_us)
            if minimum > maximum:
                raise ValueError("Pulse limits contain no representable PCA9685 tick")
            self.channels[joint] = (channel, low, high, minimum, maximum)
            seen.add(channel)

    def encode(self, pose):
        if not isinstance(pose, dict) or set(pose) != set(self.channels):
            raise ValueError("A pose must provide exactly the six calibrated joint names")
        commands = []
        for joint, (channel, low, high, minimum, maximum) in self.channels.items():
            pulse = pose[joint]
            if (
                isinstance(pulse, bool)
                or not isinstance(pulse, (int, float))
                or not math.isfinite(pulse)
                or not low <= pulse <= high
            ):
                raise ValueError(f"Pulse outside measured limits for {joint}")
            ticks = max(minimum, min(maximum, round(pulse / self.tick_us)))
            commands.append(Pulse(joint, channel, pulse, ticks * self.tick_us, ticks))
        return sorted(commands, key=lambda command: command.channel)


class Pca9685Output:
    """Low-level output only; no feedback, homing, interpolation or automatic stop.

    Opening and closing request all PWM outputs off, which may release a load.
    Use only with the arm supported and servo power disconnected during setup.
    A host crash can leave the last PWM active. No watchdog is provided here.
    """

    def __init__(
        self,
        calibration,
        *,
        bus_number=1,
        address=0x40,
        reference_clock_hz=25_000_000,
        bus_factory=None,
    ):
        self.plan = PulsePlan(calibration, reference_clock_hz)
        if type(bus_number) is not int or bus_number < 0:
            raise ValueError("Invalid I2C bus number")
        # Exclude reserved I2C addresses and the PCA9685 all-call address.
        if type(address) is not int or not 0x40 <= address <= 0x77 or address == 0x70:
            raise ValueError("Invalid individual PCA9685 I2C address")
        if bus_factory is None:
            from smbus2 import SMBus

            bus_factory = SMBus
        self.address = address
        self.closed = False
        self.faulted = False
        self.commanded = None
        self.bus = bus_factory(bus_number)
        try:
            # ALL_LED_OFF_H propagates full-off to every channel, including stale outputs.
            self.bus.write_byte_data(address, 0xFD, 0x10)
            self.bus.write_byte_data(address, 0x00, 0x30)  # SLEEP + auto increment
            self.bus.write_byte_data(address, 0x01, 0x04)  # Totem pole; changes on STOP
            self.bus.write_byte_data(address, 0xFE, self.plan.prescale)
            self.bus.write_byte_data(address, 0x00, 0x20)  # Wake, no group addresses
            time.sleep(0.005)  # Oscillator stabilization exceeds the specified 500 us.
        except Exception:
            self.faulted = self.closed = True
            self.bus.close()
            raise

    def write_pose(self, pose):
        if self.closed or self.faulted:
            raise RuntimeError("Output closed or faulted; inspect hardware before reconnecting")
        commands = self.plan.encode(pose)  # Validate every target before the first write.
        try:
            for command in commands:
                self.bus.write_i2c_block_data(
                    self.address,
                    0x06 + 4 * command.channel,
                    [0, 0, command.ticks & 0xFF, command.ticks >> 8],
                )
        except Exception as exc:
            # Some channels may already have changed; do not claim the old pose or retry.
            self.faulted = True
            self.commanded = None
            raise RuntimeError(
                "I2C write failed; physical position and output state are unknown. "
                "Use the physical cutoff and inspect the supported arm."
            ) from exc
        self.commanded = tuple(commands)  # Sent pulses only, never measured position.
        return self.commanded

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.commanded = None
        try:
            self.bus.write_byte_data(self.address, 0xFD, 0x10)
        finally:
            self.bus.close()


def main():
    parser = argparse.ArgumentParser(description="Validate a six-joint pose without opening I2C")
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--pose", type=Path, required=True)
    parser.add_argument("--reference-clock-hz", type=int, default=25_000_000)
    args = parser.parse_args()
    try:
        plan = PulsePlan(
            json.loads(args.calibration.read_text(encoding="utf-8")), args.reference_clock_hz
        )
        commands = plan.encode(json.loads(args.pose.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "mode": "dry-run",
                "measured_feedback": False,
                "frequency_hz": 1_000_000 / (4096 * plan.tick_us),
                "commands": [asdict(command) for command in commands],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
