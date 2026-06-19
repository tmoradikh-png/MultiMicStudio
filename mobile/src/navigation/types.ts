import type { Participant, SessionData } from "../api/client";

// Role on the device for the current recording. MVP records as speaker_mic; the full
// product uses host/backup_recorder/camera variations (UI-only changes later).
export type RecordRole = "host" | "speaker_mic";

export type RootStackParamList = {
  Login: undefined;
  Signup: undefined;
  Home: undefined;
  CreateSession: undefined;
  JoinSession: undefined;
  // Live speaker mode lets a phone be a microphone or a stereo listener over the
  // network. Optional room prefill is used when launching Live from a created
  // session so phones share the same code by default.
  Live: {
    room?: string;
    role?: "mic" | "speaker";
    name?: string;
    autoStart?: boolean;
  } | undefined;
  // Native WebRTC peer-to-peer live audio proof-of-concept. Server relays only
  // SDP/ICE; audio flows phone-to-phone. Requires an Expo Dev Build.
  P2PLive: {
    room?: string;
    role?: "mic" | "speaker";
    name?: string;
  } | undefined;
  // Recording screen needs the session + this device's participant identity.
  Record: {
    session: SessionData;
    participant: Participant;
    role: RecordRole;
  };
};
