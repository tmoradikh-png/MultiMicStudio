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
  // Recording screen needs the session + this device's participant identity.
  Record: {
    session: SessionData;
    participant: Participant;
    role: RecordRole;
  };
};
