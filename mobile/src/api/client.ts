import AsyncStorage from "@react-native-async-storage/async-storage";
import { API_BASE_URL } from "../config";

const TOKEN_KEY = "multimic.token";

export async function getToken(): Promise<string | null> {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export async function setToken(token: string): Promise<void> {
  await AsyncStorage.setItem(TOKEN_KEY, token);
}

export async function clearToken(): Promise<void> {
  await AsyncStorage.removeItem(TOKEN_KEY);
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = await getToken();
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
    return handle<JoinResult>(res);
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
