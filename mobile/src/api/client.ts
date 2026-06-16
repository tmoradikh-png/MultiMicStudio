import AsyncStorage from "@react-native-async-storage/async-storage";
import { API_BASE_URL } from "../config";

const TOKEN_KEY = "multimic.token";
// A no-account guest receives this opaque token when joining a session. The phone
// persists it and reuses it for status polling, uploads and upload RETRIES so the
// same device always maps to the same backend participant (no duplicate audio).
const GUEST_TOKEN_KEY = "multimic.guest_token";

export async function getToken(): Promise<string | null> {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export async function setToken(token: string): Promise<void> {
  await AsyncStorage.setItem(TOKEN_KEY, token);
}

export async function clearToken(): Promise<void> {
  await AsyncStorage.removeItem(TOKEN_KEY);
}

export async function getGuestToken(): Promise<string | null> {
  return AsyncStorage.getItem(GUEST_TOKEN_KEY);
}

export async function setGuestToken(token: string): Promise<void> {
  await AsyncStorage.setItem(GUEST_TOKEN_KEY, token);
}

export async function clearGuestToken(): Promise<void> {
  await AsyncStorage.removeItem(GUEST_TOKEN_KEY);
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

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
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
    const res = await fetch(`${API_BASE_URL}/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, name, password }),
    });
    return handle<AuthToken>(res);
  },

  async login(email: string, password: string): Promise<AuthToken> {
    const res = await fetch(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    return handle<AuthToken>(res);
  },

  async createSession(title: string): Promise<SessionData> {
    const res = await fetch(`${API_BASE_URL}/sessions`, {
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
    const res = await fetch(`${API_BASE_URL}/sessions/join`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify({
        code,
        speaker_name: speakerName,
        device_name: deviceName,
      }),
    });
    const result = await handle<JoinResult>(res);
    // Persist the guest token so reconnects / upload retries reuse the SAME
    // participant. Only stored when joining without an account.
    if (result.guest_token) {
      await setGuestToken(result.guest_token);
    }
    return result;
  },

  async getSession(id: string): Promise<SessionData> {
    const res = await fetch(`${API_BASE_URL}/sessions/${id}`, {
      headers: await authHeaders(),
    });
    return handle<SessionData>(res);
  },

  async startSession(id: string): Promise<SessionData> {
    const res = await fetch(`${API_BASE_URL}/sessions/${id}/start`, {
      method: "POST",
      headers: await authHeaders(),
    });
    return handle<SessionData>(res);
  },

  async stopSession(id: string): Promise<SessionData> {
    const res = await fetch(`${API_BASE_URL}/sessions/${id}/stop`, {
      method: "POST",
      headers: await authHeaders(),
    });
    return handle<SessionData>(res);
  },

  // Lightweight status poll usable by any participant (host or guest), so joined
  // phones can auto-start/stop when the host does.
  async getSessionStatus(id: string): Promise<SessionStatus> {
    const res = await fetch(`${API_BASE_URL}/sessions/${id}/status`, {
      headers: await authHeaders(),
    });
    return handle<SessionStatus>(res);
  },

  async processSession(id: string): Promise<unknown> {
    const res = await fetch(`${API_BASE_URL}/projects/process/${id}`, {
      method: "POST",
      headers: await authHeaders(),
    });
    return handle<unknown>(res);
  },
};

export { API_BASE_URL };
