export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "multimic.web.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export interface ProjectListItem {
  session_id: string;
  title: string;
  status: string;
  project_id: string | null;
  processing_status: string | null;
  final_audio_url: string | null;
  created_at: string;
}

export interface Stem {
  id: string;
  output_type: string;
  content: string;
  created_at: string;
}

export interface Project {
  id: string;
  session_id: string;
  final_audio_url: string | null;
  final_audio_stereo_url: string | null;
  final_audio_enhanced_url: string | null;
  enhancement_mode: string | null;
  transcript_text: string | null;
  summary_text: string | null;
  processing_status: string;
  error: string | null;
  created_at: string;
  stems: Stem[];
}

export type EnhancementMode = "natural" | "studio_voice" | "karaoke" | "party";

export interface OutputItem {
  role: string;
  label: string;
  url: string | null;
  kind: "raw" | "mix";
  available: boolean;
}

export interface QualitySummaryItem {
  question: string;
  answer: string;
  good: boolean;
}

export interface QualityBadge {
  ok: boolean;
  passed: number;
  total: number;
  failed: number;
  baseline_failed: number;
  baseline_total: number;
  summary: QualitySummaryItem[];
}

export interface ProjectOutputs {
  session_id: string;
  processing_status: string;
  outputs: OutputItem[];
  quality: QualityBadge | null;
}

export const api = {
  async login(email: string, password: string): Promise<string> {
    const res = await fetch(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await handle<{ access_token: string }>(res);
    return data.access_token;
  },

  async listProjects(): Promise<ProjectListItem[]> {
    const res = await fetch(`${API_BASE_URL}/projects`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    return handle<ProjectListItem[]>(res);
  },

  async getProject(sessionId: string): Promise<Project> {
    const res = await fetch(`${API_BASE_URL}/projects/${sessionId}`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    return handle<Project>(res);
  },

  async getOutputs(sessionId: string): Promise<ProjectOutputs> {
    const res = await fetch(`${API_BASE_URL}/projects/${sessionId}/outputs`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    return handle<ProjectOutputs>(res);
  },

  async processSession(sessionId: string): Promise<void> {
    const res = await fetch(`${API_BASE_URL}/projects/process/${sessionId}`, {
      method: "POST",
      headers: authHeaders(),
    });
    await handle<unknown>(res);
  },

  async enhanceProject(
    sessionId: string,
    mode: EnhancementMode,
  ): Promise<Project> {
    const res = await fetch(`${API_BASE_URL}/projects/${sessionId}/enhance`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    return handle<Project>(res);
  },
};

// Local file URLs from the backend are relative (/files/...); make them absolute.
export function absoluteUrl(url: string | null): string | null {
  if (!url) return null;
  if (url.startsWith("http")) return url;
  return `${API_BASE_URL}${url}`;
}
