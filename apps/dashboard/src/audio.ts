/** Capture mono PCM locally; upload WAV only after the operator finishes speaking. */
export async function record(): Promise<() => Promise<Blob>> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  let context: AudioContext;
  try {
    context = new AudioContext();
    await context.resume();
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    throw error;
  }
  const input = context.createMediaStreamSource(stream);
  // ScriptProcessor remains widely supported on local browsers; a zero-gain
  // output keeps processing active without playing microphone audio back.
  const processor = context.createScriptProcessor(4096, 1, 1);
  const mute = context.createGain();
  mute.gain.value = 0;
  const chunks: Float32Array[] = [];
  let samples = 0;
  processor.onaudioprocess = (event) => {
    const chunk = event.inputBuffer.getChannelData(0);
    if (samples + chunk.length <= context.sampleRate * 30) {
      chunks.push(new Float32Array(chunk));
      samples += chunk.length;
    }
  };
  input.connect(processor);
  processor.connect(mute);
  mute.connect(context.destination);
  return async () => {
    processor.disconnect();
    input.disconnect();
    mute.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    const rate = context.sampleRate;
    await context.close();
    const output = new ArrayBuffer(44 + samples * 2);
    const view = new DataView(output);
    const text = (offset: number, value: string) => {
      [...value].forEach((char, i) =>
        view.setUint8(offset + i, char.charCodeAt(0)),
      );
    };
    text(0, "RIFF");
    view.setUint32(4, 36 + samples * 2, true);
    text(8, "WAVE");
    text(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    text(36, "data");
    view.setUint32(40, samples * 2, true);
    let offset = 44;
    for (const chunk of chunks)
      for (const sample of chunk) {
        view.setInt16(
          offset,
          Math.max(-1, Math.min(1, sample)) * (sample < 0 ? 32768 : 32767),
          true,
        );
        offset += 2;
      }
    return new Blob([output], { type: "audio/wav" });
  };
}
