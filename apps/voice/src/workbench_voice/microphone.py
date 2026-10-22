"""Pi-attached ALSA capture. Recordings exist only in memory and in the browser."""

import asyncio
import base64
import io
import math
import re
import struct
import wave

from fastapi import HTTPException
from workbench_common.diagnostics import command
from workbench_contracts import MicrophoneDevice, MicrophoneResult


async def devices(mock=False):
    if mock:
        return [MicrophoneDevice(id="mock", name="Simulated Pi microphone")]
    output = (await command("arecord", "-l")).decode(errors="replace")
    result = []
    for line in output.splitlines():
        match = re.match(r"card \d+: (\w+) \[(.*?)\], device (\d+): (.*)", line)
        if match:
            card, name, device, detail = match.groups()
            result.append(
                MicrophoneDevice(id=f"plughw:CARD={card},DEV={device}", name=f"{name} · {detail}")
            )
    return result


def result(device_id, pcm, simulated=False):
    rate = 16000
    if len(pcm) != rate * 2 * 5:
        raise ValueError("Microphone returned empty, truncated, or oversized PCM audio")
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    peak = max(abs(sample) for sample in samples) / 32768
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return MicrophoneResult(
        device_id=device_id,
        duration_seconds=len(samples) / rate,
        sample_rate=rate,
        peak=peak,
        rms=rms,
        clipped=any(abs(s) >= 32767 for s in samples),
        wav_base64=base64.b64encode(output.getvalue()).decode(),
        simulated=simulated,
    )


class Microphone:
    def __init__(self, mock=False):
        self.mock = mock
        self.lock = asyncio.Lock()
        self.tasks = set()

    async def capture(self, device_id):
        if self.lock.locked():
            raise HTTPException(409, "Microphone test already running")
        async with self.lock:
            if device_id not in {device.id for device in await devices(self.mock)}:
                raise HTTPException(422, "Select an available Pi capture device")
            if self.mock:
                await asyncio.sleep(5)
                pcm = b"".join(
                    struct.pack("<h", int(3000 * math.sin(i * 2 * math.pi * 440 / 16000)))
                    for i in range(80000)
                )
            else:
                pcm = await command(
                    "arecord",
                    "-q",
                    "-D",
                    device_id,
                    "-t",
                    "raw",
                    "-f",
                    "S16_LE",
                    "-r",
                    "16000",
                    "-c",
                    "1",
                    "-d",
                    "5",
                    timeout=8,
                )
            return result(device_id, pcm, self.mock)

    async def test(self, request, device_id):
        task = asyncio.create_task(self.capture(device_id))
        self.tasks.add(task)
        try:
            while not task.done():
                if await request.is_disconnected():
                    raise HTTPException(499, "Microphone client disconnected")
                await asyncio.wait({task}, timeout=0.1)
            return await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.tasks.discard(task)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
