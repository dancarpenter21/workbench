export function mountRecording(video: HTMLVideoElement) {
  const button = document.querySelector<HTMLButtonElement>("#camera-record")!;
  const status = document.querySelector<HTMLElement>("#recording-status")!;
  const codec = typeof MediaRecorder === "undefined" ? undefined :
    ["video/mp4;codecs=avc1.424028", "video/mp4;codecs=avc1"].find(type => MediaRecorder.isTypeSupported(type));
  let recorder: MediaRecorder | undefined;
  let timer = 0;
  let live = false;
  let busy = false;
  function refresh() {
    button.disabled = !codec || busy || (!recorder && !live);
  }
  if (!codec) status.textContent = "MP4 recording is unavailable in this browser. Use a browser with H.264 MP4 recording support.";
  function stop() {
    if (!recorder || recorder.state === "inactive") return;
    busy = true;
    clearInterval(timer);
    recorder.stop();
    status.textContent = "Saving recording…";
    refresh();
  }
  button.addEventListener("click", () => {
    if (recorder) { stop(); return; }
    if (!codec || !live || !(video.srcObject instanceof MediaStream)) return;
    const chunks: Blob[] = [];
    const started = Date.now();
    let failed = false;
    try {
      const current = new MediaRecorder(new MediaStream(video.srcObject.getVideoTracks()), {
        mimeType: codec, videoBitsPerSecond: 2_000_000,
      });
      recorder = current;
      current.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      current.onerror = () => { failed = true; stop(); };
      current.onstop = () => {
        clearInterval(timer);
        if (chunks.length) {
          const url = URL.createObjectURL(new Blob(chunks, { type: current.mimeType }));
          const link = document.createElement("a");
          link.href = url;
          link.download = `workbench-demo-${new Date(started).toISOString().replace(/[:.]/g, "-")}.mp4`;
          link.click();
          window.setTimeout(() => URL.revokeObjectURL(url), 60000);
        }
        status.textContent = failed ? "Recording failed. Any available video was downloaded." :
          chunks.length ? "Recording downloaded." : "No video was recorded.";
        chunks.length = 0;
        recorder = undefined;
        busy = false;
        button.setAttribute("aria-label", "Record camera video");
        button.title = "Record camera video";
        button.setAttribute("aria-pressed", "false");
        refresh();
      };
      current.start(1000);
      button.setAttribute("aria-label", "Stop recording");
      button.title = "Stop recording";
      button.setAttribute("aria-pressed", "true");
      const update = () => { status.textContent = `Recording · ${Math.floor((Date.now() - started) / 1000)} s`; };
      update();
      timer = window.setInterval(update, 1000);
    } catch {
      recorder = undefined;
      status.textContent = "Unable to start MP4 recording in this browser.";
    }
    refresh();
  });
  window.addEventListener("beforeunload", event => {
    if (recorder) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pagehide", stop);
  refresh();
  return (available: boolean) => {
    live = available;
    if (!live) stop();
    refresh();
  };
}
