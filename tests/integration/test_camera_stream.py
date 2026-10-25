import asyncio
import threading
from datetime import timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from fastapi import HTTPException
from workbench_common import settings
from workbench_contracts import MockScene, Observations, VideoOffer, now
from workbench_vision.app import Camera, create_app
from workbench_vision.stream import CameraTrack, VideoSessions


def config(mode="mock"):
    cfg = settings()
    cfg["mode"] = mode
    return cfg


async def test_video_signaling_decodes_frames_and_cleans_up():
    app = create_app(config())
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    received = asyncio.get_running_loop().create_future()

    @peer.on("track")
    def track(track):
        received.set_result(track)

    peer.addTransceiver("video", direction="recvonly")
    try:
        await peer.setLocalDescription(await peer.createOffer())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/webrtc/offer",
                    json={
                        "type": "offer",
                        "sdp": peer.localDescription.sdp,
                    },
                )
                assert response.status_code == 200
                answer = response.json()
                assert answer["simulated"]
                await peer.setRemoteDescription(
                    RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
                )
                track = await asyncio.wait_for(received, 5)
                first = await asyncio.wait_for(track.recv(), 5)
                second = await asyncio.wait_for(track.recv(), 5)
                assert first.width == config()["camera"]["width"]
                assert second.pts > first.pts
                assert not np.array_equal(first.to_ndarray(), second.to_ndarray())
                assert (
                    await client.delete(f"/webrtc/sessions/{answer['session_id']}")
                ).status_code == 204
    finally:
        await peer.close()


async def test_bad_offer_releases_session_and_track():
    sessions = VideoSessions(config(), lambda: None, lambda: MockScene(tools=[]))
    with pytest.raises(HTTPException):
        await sessions.offer(VideoOffer(sdp="invalid"))
    assert not sessions.peers
    assert not sessions.tasks
    await sessions.close()


async def test_track_skips_superseded_frames_and_stops_on_camera_failure():
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    sample = ("first", frame)
    camera = SimpleNamespace(latest=lambda: sample)
    track = CameraTrack(config("hardware"), lambda: camera, lambda: None)
    first = await track.recv()
    pending = asyncio.create_task(track.recv())
    await asyncio.sleep(0.05)
    assert not pending.done()  # An unchanged frame cannot masquerade as a fresh capture.
    sample = None
    await asyncio.sleep(0.05)
    assert not pending.done()
    sample = ("latest", np.full_like(frame, 200))
    second = await asyncio.wait_for(pending, 1)
    assert second.pts > first.pts
    assert second.to_ndarray(format="bgr24")[0, 0, 0] == 200
    track.stop()


def test_slow_recognition_uses_capture_time_and_cannot_restore_failed_camera():
    camera = Camera.__new__(Camera)
    camera.cfg = config("hardware")
    camera.lock = threading.Lock()
    camera.stop = threading.Event()
    camera.recognition_error = ""
    camera.error = ""
    camera.frame = np.zeros((20, 20, 3), dtype=np.uint8)
    camera.frame_id = "first"
    camera.frame_at = now() - timedelta(seconds=0.2)
    camera.timestamp_source = "sensor"
    started, finish = threading.Event(), threading.Event()
    camera.observations = Observations(
        captured_at=now(), tools=[], tray_clear=False, camera_ok=False
    )

    def recognize(frame):
        started.set()
        assert finish.wait(2)
        camera.stop.set()
        return Observations(captured_at=now(), tools=[], tray_clear=True, camera_ok=True)

    camera.recognize = recognize
    worker = threading.Thread(target=camera.run_recognition)
    worker.start()
    try:
        assert started.wait(1)
        assert camera.latest()[0] == "first"  # Inference does not hold the capture lock.
        finish.set()
        worker.join(2)
        assert camera.observations.captured_at == camera.frame_at
        assert camera.observations.camera_ok
    finally:
        finish.set()
        camera.stop.set()
        worker.join(2)

    camera.stop.clear()
    finish.clear()
    started.clear()
    worker = threading.Thread(target=camera.run_recognition)
    worker.start()
    try:
        assert started.wait(1)
        camera.record_error(RuntimeError("disconnected"))
        finish.set()
        worker.join(2)
        assert not camera.observations.camera_ok
        assert camera.latest() is None
    finally:
        finish.set()
        camera.stop.set()
        worker.join(2)


async def test_session_shutdown_stops_tracks_and_closes_peers():
    sessions = VideoSessions(config(), lambda: None, lambda: MockScene(tools=[]))
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    peer.addTransceiver("video", direction="recvonly")
    try:
        await peer.setLocalDescription(await peer.createOffer())
        answer = await sessions.offer(VideoOffer(sdp=peer.localDescription.sdp))
        sender, track = sessions.peers[answer.session_id]
        await sessions.close()
        assert sender.connectionState == "closed"
        assert track.readyState == "ended"
        assert not sessions.peers
        assert not sessions.tasks
    finally:
        await peer.close()


async def test_coordinator_forwards_video_negotiation_and_cleanup(stack):
    bus, cfg, calls, _, _ = stack
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    peer.addTransceiver("video", direction="recvonly")
    base = cfg["services"]["coordinator"]
    try:
        await peer.setLocalDescription(await peer.createOffer())
        response = await bus.post(
            base + "/camera/webrtc/offer",
            json={
                "type": "offer",
                "sdp": peer.localDescription.sdp,
            },
        )
        assert response.status_code == 200
        answer = response.json()
        assert answer["type"] == "answer"
        assert answer["simulated"]
        assert (
            await bus.delete(base + f"/camera/webrtc/sessions/{answer['session_id']}")
        ).status_code == 204
        assert ("POST", cfg["services"]["vision"] + "/webrtc/offer") in calls
    finally:
        await peer.close()


async def test_abandoned_session_expires(monkeypatch):
    from workbench_vision import stream

    sessions = VideoSessions(config(), lambda: None, lambda: MockScene(tools=[]))
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    track = CameraTrack(config(), lambda: None, lambda: MockScene(tools=[]))
    sessions.peers["abandoned"] = (peer, track)
    monkeypatch.setattr(stream, "SESSION_TIMEOUT_SECONDS", 0)
    await asyncio.wait_for(sessions.watch("abandoned", peer), timeout=2)
    assert not sessions.peers
    assert track.readyState == "ended"


async def test_unavailable_camera_rejects_video_without_allocating_peer():
    sessions = VideoSessions(config("hardware"), lambda: None, lambda: None)
    with pytest.raises(HTTPException) as error:
        await sessions.offer(VideoOffer(sdp="unused"))
    assert error.value.status_code == 503
    assert not sessions.peers


async def test_missing_viewer_heartbeat_releases_session(monkeypatch):
    from workbench_vision import stream

    sessions = VideoSessions(config(), lambda: None, lambda: MockScene(tools=[]))
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    track = CameraTrack(config(), lambda: None, lambda: MockScene(tools=[]))
    sessions.peers["silent"] = (peer, track)
    sessions.controls["silent"] = (SimpleNamespace(readyState="open"), 0)
    monkeypatch.setattr(stream, "HEARTBEAT_TIMEOUT_SECONDS", 0)
    await asyncio.wait_for(sessions.watch("silent", peer), timeout=2)
    assert not sessions.peers
    assert not sessions.controls
    assert track.readyState == "ended"
