import { Audio } from "expo-av";
// expo-file-system v19 (SDK 54) moved the classic functional API
// (uploadAsync / getInfoAsync / FileSystemUploadType) to the "/legacy" entry.
// Importing from the main module leaves these undefined, which is why
// "Stop & upload" silently failed before any request was sent.
import * as FileSystem from "expo-file-system/legacy";
import { API_BASE_URL } from "../config";
import { getToken } from "./client";

// One phone per speaker: record locally at high quality, keep a local backup,
// then upload with retry. Mirrors the MVP recording flow in the spec.
const RECORDING_OPTIONS: Audio.RecordingOptions = {
  ...Audio.RecordingOptionsPresets.HIGH_QUALITY,
  android: {
    ...Audio.RecordingOptionsPresets.HIGH_QUALITY.android,
    sampleRate: 48000,
  },
  ios: {
    ...Audio.RecordingOptionsPresets.HIGH_QUALITY.ios,
    sampleRate: 48000,
  },
};

export interface FinishedRecording {
  uri: string;
  durationSeconds: number;
  startedAtMs: number;
}

export class Recorder {
  private recording: Audio.Recording | null = null;
  private startedAtMs = 0;

  async start(): Promise<void> {
    const perm = await Audio.requestPermissionsAsync();
    if (!perm.granted) {
      throw new Error("Microphone permission denied");
    }
    await Audio.setAudioModeAsync({
      allowsRecordingIOS: true,
      playsInSilentModeIOS: true,
    });
    const recording = new Audio.Recording();
    await recording.prepareToRecordAsync(RECORDING_OPTIONS);
    await recording.startAsync();
    this.recording = recording;
    this.startedAtMs = Date.now();
  }

  async stop(): Promise<FinishedRecording> {
    if (!this.recording) throw new Error("Not recording");
    await this.recording.stopAndUnloadAsync();
    const status = await this.recording.getStatusAsync();
    const uri = this.recording.getURI();
    this.recording = null;
    if (!uri) throw new Error("Recording produced no file");
    const durationSeconds =
      "durationMillis" in status && status.durationMillis
        ? status.durationMillis / 1000
        : 0;
    return { uri, durationSeconds, startedAtMs: this.startedAtMs };
  }

  isRecording(): boolean {
    return this.recording !== null;
  }
}

// --- Sync beep ---------------------------------------------------------------
// A short, loud tone played by the host and captured by every phone's mic.
// Because it is one physical sound, the backend can align all tracks to it at
// sample accuracy — independent of when each phone actually started recording.

const BEEP_FREQ_HZ = 1000;
const BEEP_MS = 250;
const BEEP_SR = 44100;

function base64FromBytes(bytes: Uint8Array): string {
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let out = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const b0 = bytes[i];
    const b1 = i + 1 < bytes.length ? bytes[i + 1] : 0;
    const b2 = i + 2 < bytes.length ? bytes[i + 2] : 0;
    out += chars[b0 >> 2];
    out += chars[((b0 & 3) << 4) | (b1 >> 4)];
    out += i + 1 < bytes.length ? chars[((b1 & 15) << 2) | (b2 >> 6)] : "=";
    out += i + 2 < bytes.length ? chars[b2 & 63] : "=";
  }
  return out;
}

function buildBeepWavBase64(): string {
  const numSamples = Math.floor((BEEP_SR * BEEP_MS) / 1000);
  const dataLen = numSamples * 2; // 16-bit mono
  const buf = new Uint8Array(44 + dataLen);
  const view = new DataView(buf.buffer);
  const writeStr = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataLen, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, BEEP_SR, true);
  view.setUint32(28, BEEP_SR * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, dataLen, true);
  // Apply a short fade in/out so the tone has a clean, detectable onset.
  for (let i = 0; i < numSamples; i++) {
    const env = Math.min(1, i / 200, (numSamples - i) / 200);
    const sample = Math.sin((2 * Math.PI * BEEP_FREQ_HZ * i) / BEEP_SR) * env;
    view.setInt16(44 + i * 2, Math.round(sample * 32767 * 0.9), true);
  }
  return base64FromBytes(buf);
}

let cachedBeepUri: string | null = null;

async function getBeepUri(): Promise<string> {
  if (cachedBeepUri) return cachedBeepUri;
  const uri = `${FileSystem.cacheDirectory}sync_beep.wav`;
  await FileSystem.writeAsStringAsync(uri, buildBeepWavBase64(), {
    encoding: FileSystem.EncodingType.Base64,
  });
  cachedBeepUri = uri;
  return uri;
}

/**
 * Play the audible sync beep through the speaker (host only). All phones must
 * already be recording so each mic captures it; the backend aligns to its onset.
 */
export async function playSyncBeep(): Promise<void> {
  // Route playback to the loud speaker while recording stays active.
  await Audio.setAudioModeAsync({
    allowsRecordingIOS: true,
    playsInSilentModeIOS: true,
    playThroughEarpieceAndroid: false,
  });
  const uri = await getBeepUri();
  const { sound } = await Audio.Sound.createAsync(
    { uri },
    { shouldPlay: true, volume: 1.0 },
  );
  // Unload shortly after it finishes to free resources.
  sound.setOnPlaybackStatusUpdate((status) => {
    if ("didJustFinish" in status && status.didJustFinish) {
      sound.unloadAsync().catch(() => undefined);
    }
  });
}

// Upload with simple retry. The backend contract (register+upload in one request)
// is ready to evolve into resumable chunk uploads without changing this call site.
export async function uploadRecording(
  sessionId: string,
  participantId: string,
  takeId: string | null,
  rec: FinishedRecording,
  maxRetries = 3,
): Promise<void> {
  const token = await getToken();
  const fileInfo = await FileSystem.getInfoAsync(rec.uri);
  if (!fileInfo.exists) throw new Error("Local recording file missing");

  let attempt = 0;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    try {
      const result = await FileSystem.uploadAsync(
        `${API_BASE_URL}/recordings`,
        rec.uri,
        {
          httpMethod: "POST",
          uploadType: FileSystem.FileSystemUploadType.MULTIPART,
          fieldName: "file",
          mimeType: "audio/m4a",
          parameters: {
            session_id: sessionId,
            participant_id: participantId,
            ...(takeId ? { take_id: takeId } : {}),
            local_start_timestamp: String(rec.startedAtMs),
            duration_seconds: String(rec.durationSeconds),
            sample_rate: "48000",
          },
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      if (result.status >= 200 && result.status < 300) return;
      // Surface the backend's error body so the phone shows the real reason.
      const detail = (result.body || "").slice(0, 300);
      throw new Error(
        `Upload failed (HTTP ${result.status})${detail ? `: ${detail}` : ""}`,
      );
    } catch (err) {
      attempt += 1;
      if (attempt >= maxRetries) throw err;
      await new Promise((r) => setTimeout(r, 1000 * attempt));
    }
  }
}
