import AsyncStorage from "@react-native-async-storage/async-storage";
import { API_BASE_URL } from "../config";

const TOKEN_KEY = "multimic.token";
// A no-account guest receives this opaque token when joining a session. The phone
// persists it (bound to the session code) and reuses it for status polling, uploads
// and upload RETRIES — and across app restarts — so the same device always maps to
// the same backend participant (no duplicate audio).
const GUEST_KEY = "multimic.guest";

interface GuestCreds {
  code: string;
  token: string;
}

async function readGuest(): Promise<GuestCreds | null> {
  const raw = await AsyncStorage.getItem(GUEST_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as GuestCreds;
  } catch {
    return null;
  }
}

export async function getToken(): Promise<string | null> {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export async function setToken(token: string): Promise<void> {
  await AsyncStorage.setItem(TOKEN_KEY, token);
}

export async function clearToken(): Promise<void> {
  await AsyncStorage.removeItem(TOKEN_KEY);
}

// The active guest token (used by uploads/status, which key off session id only).
export async function getGuestToken(): Promise<string | null> {
  return (await readGuest())?.token ?? null;
}

// The guest token previously issued for THIS session code, if any. Sent on re-join
// so a restarted app reconnects to its existing participant instead of duplicating.
export async function getGuestTokenForCode(code: string): Promise<string | null> {
  const g = await readGuest();
  return g && g.code === code.toUpperCase() ? g.token : null;
}

async function setGuestToken(code: string, token: string): Promise<void> {
  await AsyncStorage.setItem(
    GUEST_KEY,
    JSON.stringify({ code: code.toUpperCase(), token } satisfies GuestCreds),
  );
}

export async function clearGuestToken(): Promise<void> {
  await AsyncStorage.removeItem(GUEST_KEY);
}

// The phone remembers the session it is actively recording in, so a crash or an
// app restart can drop the user straight back into it (re-using the same guest
// token / participant — never a duplicate). Cleared when the user leaves the
// session or signs out.
const ACTIVE_KEY = "multimic.active";

export interface ActiveSession {
  sessionId: string;
  code: string;
  participantId: string;
  speakerName: string;
  role: "host" | "speaker_mic";
  isGuest: boolean;
}

export async function setActiveSession(active: ActiveSession): Promise<void> {
  await AsyncStorage.setItem(ACTIVE_KEY, JSON.stringify(active));
}

export async function getActiveSession(): Promise<ActiveSession | null> {
  const raw = await AsyncStorage.getItem(ACTIVE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ActiveSession;
  } catch {
    return null;
  }
}

export async function clearActiveSession(): Promise<void> {
  await AsyncStorage.removeItem(ACTIVE_KEY);
}

// The credential to send on session/recording calls: a logged-in account token if
// present, otherwise the stored guest token. This is what lets a no-account phone
// authenticate using only the token it got when it joined.
export async function getAuthToken(): Promise<string | null> {
  return (await getToken()) ?? (await getGuestToken());
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = await getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// A network/HTTP failure with the status preserved, so the UI can show a clear,
// specific message (unreachable backend vs. bad code vs. expired session, etc).
// status 0 means the request never reached the server (no connection / wrong host).
export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// fetch() that turns a dropped connection / unreachable host into an ApiError
// with status 0 instead of an opaque "Network request failed" TypeError.
async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new ApiError(0, "Network unreachable");
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(
      res.status,
      typeof detail === "string" ? detail : "Request failed",
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// Map any thrown error into a clear, user-facing sentence. Keeps the backend's
// own message when it is already specific (invalid code, session closed, etc).
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    switch (true) {
      case err.status === 0:
        return "Can't reach the server. Check your Wi‑Fi and that the host's backend is running, then try again.";
      case err.status === 401:
        return "Your session access expired. Re‑join with the session code to continue.";
      case err.status === 403:
        return "You don't have access to this session.";
      case err.status === 404:
        return err.detail || "Not found — the session may have ended.";
      case err.status === 409:
        return err.detail || "This session is no longer open to join. Ask the host for a new code.";
      case err.status === 413:
        return "That recording is too large or too long to upload. Try a shorter take.";
      case err.status === 415:
        return err.detail || "That file type isn't supported. Please record audio in the app.";
      case err.status === 422:
        return err.detail || "That recording couldn't be read. Please record again.";
      case err.status >= 500:
        return "The server hit a problem. Please wait a moment and try again.";
      default:
        return err.detail || "Something went wrong.";
    }
  }
  return err instanceof Error ? err.message : "Something went wrong.";
}

export interface AuthToken {
  access_token: string;
  token_type: string;
}

export interface Participant {
  id: string;
  speaker_name: string;
  device_name: string;
  role: string;
  joined_at: string;
}

export interface SessionData {
  id: string;
  title: string;
  code: string;
  status: string;
  current_take_id: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  participants: Participant[];
}

export interface JoinResult {
  session: SessionData;
  participant: Participant;
  // Present only for no-account guests; null when a logged-in user joins.
  guest_token: string | null;
}

export interface SessionStatus {
  id: string;
  status: string;
  current_take_id: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export const api = {
  async signup(email: string, name: string, password: string): Promise<AuthToken> {
    const res = await safeFetch(`${API_BASE_URL}/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, name, password }),
    });
    return handle<AuthToken>(res);
  },

  async login(email: string, password: string): Promise<AuthToken> {
    const res = await safeFetch(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    return handle<AuthToken>(res);
  },

  async createSession(title: string): Promise<SessionData> {
    const res = await safeFetch(`${API_BASE_URL}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify({ title }),
    });
    return handle<SessionData>(res);
  },

  async joinSession(
    code: string,
    speakerName: string,
    deviceName: string,
  ): Promise<JoinResult> {
    const normCode = code.toUpperCase();
    // Reconnect path: if this device already holds a guest token for this session
    // (e.g. after an app restart or a dropped connection), send it so the backend
    // returns the SAME participant instead of creating a duplicate phone.
    const account = await getToken();
    const reuseToken = account ? null : await getGuestTokenForCode(normCode);
    const auth = account ?? reuseToken;
    const res = await safeFetch(`${API_BASE_URL}/sessions/join`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
      },
      body: JSON.stringify({
        code: normCode,
        speaker_name: speakerName,
        device_name: deviceName,
      }),
    });
    const result = await handle<JoinResult>(res);
    // Persist the guest token (bound to this code) so reconnects / upload retries
    // reuse the SAME participant. Only stored when joining without an account.
    if (result.guest_token) {
      await setGuestToken(normCode, result.guest_token);
    }
    return result;
  },

  async getSession(id: string): Promise<SessionData> {
    const res = await safeFetch(`${API_BASE_URL}/sessions/${id}`, {
      headers: await authHeaders(),
    });
    return handle<SessionData>(res);
  },

  async startSession(id: string): Promise<SessionData> {
    const res = await safeFetch(`${API_BASE_URL}/sessions/${id}/start`, {
      method: "POST",
      headers: await authHeaders(),
    });
    return handle<SessionData>(res);
  },

  async stopSession(id: string): Promise<SessionData> {
    const res = await safeFetch(`${API_BASE_URL}/sessions/${id}/stop`, {
      method: "POST",
      headers: await authHeaders(),
    });
    return handle<SessionData>(res);
  },

  // Lightweight status poll usable by any participant (host or guest), so joined
  // phones can auto-start/stop when the host does.
  async getSessionStatus(id: string): Promise<SessionStatus> {
    const res = await safeFetch(`${API_BASE_URL}/sessions/${id}/status`, {
      headers: await authHeaders(),
    });
    return handle<SessionStatus>(res);
  },

  async processSession(id: string): Promise<unknown> {
    const res = await safeFetch(`${API_BASE_URL}/projects/process/${id}`, {
      method: "POST",
      headers: await authHeaders(),
    });
    return handle<unknown>(res);
  },
};

// Rebuild the live params for the Record screen from a stored ActiveSession, so a
// restarted app can resume. Guests re-join by code (the backend reconnect path
// returns the SAME participant via the stored token); hosts re-fetch the session.
// Returns null if the session can no longer be resumed (ended/closed/unknown).
export async function resumeSession(
  active: ActiveSession,
): Promise<JoinResult | null> {
  try {
    if (active.isGuest) {
      // Reconnect: stored guest token => same participant, current session state.
      return await api.joinSession(active.code, active.speakerName, "mobile");
    }
    const session = await api.getSession(active.sessionId);
    const participant =
      session.participants.find((p) => p.id === active.participantId) ??
      session.participants.find((p) => p.role === "host") ??
      session.participants[0];
    if (!participant) return null;
    return { session, participant, guest_token: null };
  } catch (err) {
    // A closed/ended/deleted session (404/409) is not resumable — clear it.
    if (err instanceof ApiError && [404, 409, 403].includes(err.status)) {
      await clearActiveSession();
      return null;
    }
    throw err;
  }
}

export { API_BASE_URL };
