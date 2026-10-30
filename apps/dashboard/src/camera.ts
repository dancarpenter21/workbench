import { mountRecording } from "./recording";
import type { components } from "@workbench/contracts";

type VideoAnswer = components["schemas"]["VideoAnswer"];

export function mountCamera() {
  const video = document.querySelector<HTMLVideoElement>("#camera")!;
  const status = document.querySelector<HTMLElement>("#camera-status")!;
  const note = document.querySelector<HTMLElement>("#camera-note")!;
  const timing = document.querySelector<HTMLElement>("#camera-timing")!;
  video.hidden = false;
  video.style.visibility = "hidden";
  const recordingAvailable = mountRecording(video);
  let closed = false;
  let disposeAttempt = () => {};
  let retryTimer = 0;
  let backoff = 1000;

  async function connect() {
    if (closed) return;
    let peer: RTCPeerConnection | undefined;
    let sessionId: string | undefined;
    let disposed = false;
    let callback = 0;
    let monitor = 0;
    let lastFrame = 0;
    let frames: number[] = [];
    let answer: VideoAnswer | undefined;
    const controller = new AbortController();
    const deadline = window.setTimeout(() => fail(), 15000);

    function cleanup() {
      if (disposed) return;
      disposed = true;
      recordingAvailable(false);
      clearTimeout(deadline);
      clearInterval(monitor);
      controller.abort();
      video.cancelVideoFrameCallback?.(callback);
      peer?.close();
      video.srcObject = null;
      if (sessionId) void fetch(`/api/coordinator/camera/webrtc/sessions/${sessionId}`, {
        method: "DELETE", keepalive: true,
      }).catch(() => {});
    }
    function fail() {
      if (disposed || closed) return;
      cleanup();
      video.style.visibility = "hidden";
      status.textContent = "Camera unavailable · retrying video connection";
      status.dataset.state = "error";
      timing.textContent = "No live video received. Sensor-to-display delay unavailable.";
      timing.dataset.state = "stale";
      note.hidden = false;
      note.textContent = "Check the camera, vision service, and local network connection.";
      retryTimer = window.setTimeout(() => void connect(), backoff);
      backoff = Math.min(backoff * 2, 10000);
    }
    disposeAttempt = cleanup;

    function displayed(now: number) {
      if (disposed) return;
      if (!lastFrame) {
        clearTimeout(deadline);
        video.style.visibility = "visible";
        backoff = 1000;
      }
      video.style.visibility = "visible";
      lastFrame = now;
      recordingAvailable(true);
      frames.push(now);
      frames = frames.filter(t => now - t < 3000);
      const fps = frames.length > 1 ? (frames.length - 1) * 1000 / (now - frames[0]) : 0;
      status.dataset.state = "live";
      status.textContent = `Live · WebRTC · ${fps.toFixed(1)} preview fps · target ${answer?.target_fps ?? 30}`;
      note.hidden = !answer?.simulated;
      note.textContent = answer?.simulated ? "Simulated scene · animated test video" : "";
      timing.dataset.state = "live";
      callback = video.requestVideoFrameCallback(displayed);
    }

    try {
      if (!video.requestVideoFrameCallback) throw new Error("Video frame monitoring unsupported");
      peer = new RTCPeerConnection({ iceServers: [] });
      peer.addTransceiver("video", { direction: "recvonly" });
      const control = peer.createDataChannel("camera-control");
      control.onmessage = event => {
        if (event.data === "ping" && control.readyState === "open") control.send("pong");
      };
      peer.onconnectionstatechange = () => {
        if (peer?.connectionState === "failed" || peer?.connectionState === "closed") fail();
      };
      peer.ontrack = event => {
        if (disposed) return;
        status.textContent = "Connecting to video…";
        video.style.visibility = "visible";
        video.srcObject = new MediaStream([event.track]);
        callback = video.requestVideoFrameCallback(displayed);
        void video.play().catch(fail);
      };
      await peer.setLocalDescription(await peer.createOffer());
      await new Promise<void>((resolve, reject) => {
        if (peer!.iceGatheringState === "complete") return resolve();
        peer!.onicegatheringstatechange = () => {
          if (peer!.iceGatheringState === "complete") resolve();
        };
        controller.signal.addEventListener("abort", () => reject(new Error("Video timed out")), { once: true });
      });
      if (disposed) return;
      const response = await fetch("/api/coordinator/camera/webrtc/offer", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "offer", sdp: peer.localDescription!.sdp }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("Video unavailable");
      answer = await response.json() as VideoAnswer;
      sessionId = answer.session_id;
      if (disposed) {
        void fetch(`/api/coordinator/camera/webrtc/sessions/${sessionId}`, { method: "DELETE", keepalive: true }).catch(() => {});
        return;
      }
      await peer.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
      monitor = window.setInterval(() => {
        if (lastFrame && performance.now() - lastFrame >= 3000) {
          recordingAvailable(false);
          status.textContent = "Camera stalled · no new video frames";
          status.dataset.state = "stale";
          video.style.visibility = "hidden";
          timing.dataset.state = "stale";
          if (performance.now() - lastFrame >= 6000) fail();
        }
        void peer!.getStats().then(stats => {
          if (disposed) return;
          stats.forEach(report => {
            if (report.type !== "inbound-rtp" || report.kind !== "video") return;
            const delay = report.jitterBufferEmittedCount > 0
              ? `${(1000 * report.jitterBufferDelay / report.jitterBufferEmittedCount).toFixed(1)} ms mean jitter buffer`
              : "Jitter buffer timing unavailable";
            timing.textContent = `${answer?.simulated ? "SIMULATED timing · " : ""}${report.framesDecoded ?? 0} decoded frames · ${report.packetsLost ?? 0} lost packets · ${delay} · sensor-to-display delay not measured`;
          });
        }).catch(() => {});
      }, 500);
    } catch {
      fail();
    }
  }

  window.addEventListener("pagehide", () => {
    closed = true;
    clearTimeout(retryTimer);
    disposeAttempt();
  }, { once: true });
  void connect();
}
