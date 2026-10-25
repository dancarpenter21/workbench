"""Local WebRTC video. Each viewer pulls the newest frame without a frame queue."""

import asyncio
import time
from fractions import Fraction
from uuid import uuid4

import numpy as np
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame
from fastapi import HTTPException
from workbench_contracts import VideoAnswer

SESSION_TIMEOUT_SECONDS = 20
MAX_VIEWERS = 4
HEARTBEAT_TIMEOUT_SECONDS = 3


class CameraTrack(VideoStreamTrack):
    def __init__(self, cfg, camera, scene):
        super().__init__()
        self.cfg, self.camera, self.scene = cfg, camera, scene
        self.previous = None
        self.started = time.monotonic()
        self.last_sent = self.started - 1

    async def recv(self):
        fps = self.cfg["camera"].get("fps", 30)
        await asyncio.sleep(max(0, self.last_sent + 1 / fps - time.monotonic()))
        while self.readyState == "live":
            tick = time.monotonic()
            if self.cfg["mode"] == "mock":
                # Moving stripe and scene-dependent colors exercise actual video decoding.
                width, height = self.cfg["camera"]["width"], self.cfg["camera"]["height"]
                pixels = np.full((height, width, 3), (40, 60, 25), dtype=np.uint8)
                x = int((tick - self.started) * 120) % width
                pixels[:, x : min(x + 20, width)] = (60, 210, 170)
                for i, tool in enumerate(self.scene().tools):
                    y = 40 + i * 60
                    pixels[y : y + 30, 30 : width // 2] = (
                        (70, 180, 100) if tool.location == "source" else (180, 90, 60)
                    )
                sample = (tick, pixels)
            else:
                camera = self.camera()
                sample = camera.latest() if camera else None
            if sample is not None and sample[0] != self.previous:
                self.previous = sample[0]
                frame = VideoFrame.from_ndarray(sample[1], format="bgr24")
                frame.pts = int((tick - self.started) * 90000)
                frame.time_base = Fraction(1, 90000)
                self.last_sent = tick
                return frame
            await asyncio.sleep(0.005)
        raise MediaStreamError


class VideoSessions:
    def __init__(self, cfg, camera, scene):
        self.cfg, self.camera, self.scene = cfg, camera, scene
        self.peers = {}
        self.tasks = {}
        self.controls = {}

    async def remove(self, session_id):
        task = self.tasks.pop(session_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        self.controls.pop(session_id, None)
        entry = self.peers.pop(session_id, None)
        if entry:
            peer, track = entry
            track.stop()
            await peer.close()

    async def watch(self, session_id, peer):
        try:
            # Expire negotiations that never connect, then abandon disconnected peers.
            deadline = time.monotonic() + SESSION_TIMEOUT_SECONDS
            while session_id in self.peers:
                await asyncio.sleep(1)
                control = self.controls.get(session_id)
                if control:
                    channel, heard_at = control
                    if (
                        channel.readyState != "open"
                        or time.monotonic() - heard_at > HEARTBEAT_TIMEOUT_SECONDS
                    ):
                        await self.remove(session_id)
                        return
                    channel.send("ping")
                if peer.connectionState == "connected":
                    deadline = time.monotonic() + SESSION_TIMEOUT_SECONDS
                if peer.connectionState in {"failed", "closed"} or time.monotonic() > deadline:
                    await self.remove(session_id)
                    return
        except asyncio.CancelledError:
            pass

    async def offer(self, value):
        if self.cfg["mode"] == "hardware":
            camera = self.camera()
            if camera is None or camera.latest() is None:
                raise HTTPException(503, "Camera has no live frames")
        if len(self.peers) >= MAX_VIEWERS:
            raise HTTPException(503, "Camera viewer limit reached")
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        track = CameraTrack(self.cfg, self.camera, self.scene)
        session_id = uuid4().hex

        @peer.on("datachannel")
        def control_channel(channel):
            if channel.label != "camera-control":
                channel.close()
                return
            self.controls[session_id] = (channel, time.monotonic())

            @channel.on("message")
            def heartbeat(message):
                if message == "pong" and session_id in self.peers:
                    self.controls[session_id] = (channel, time.monotonic())

            @channel.on("close")
            def disconnected():
                asyncio.create_task(self.remove(session_id))

        self.peers[session_id] = (peer, track)
        self.tasks[session_id] = asyncio.create_task(self.watch(session_id, peer))
        try:
            async with asyncio.timeout(10):
                await peer.setRemoteDescription(
                    RTCSessionDescription(sdp=value.sdp, type=value.type)
                )
                if not any(t.kind == "video" for t in peer.getTransceivers()):
                    raise ValueError("Offer must request video")
                peer.addTrack(track)
                await peer.setLocalDescription(await peer.createAnswer())
            return VideoAnswer(
                sdp=peer.localDescription.sdp,
                session_id=session_id,
                simulated=self.cfg["mode"] == "mock",
                target_fps=self.cfg["camera"].get("fps", 30),
            )
        except asyncio.CancelledError:
            await self.remove(session_id)
            raise
        except Exception as exc:
            await self.remove(session_id)
            raise HTTPException(400, "Invalid or unsupported video offer") from exc

    async def close(self):
        await asyncio.gather(*(self.remove(key) for key in list(self.peers)))
