// API base URL resolution.
//
// Priority:
//   1. EXPO_PUBLIC_API_URL  — set via env / .env / EAS build profile. This lets the
//      SAME app point at the hosted backend without editing source. Expo statically
//      inlines any EXPO_PUBLIC_* variable at build time (SDK 49+), so it works in
//      Expo Go dev and in production builds alike.
//   2. DEFAULT_API_BASE_URL — the local LAN backend, so local testing keeps working
//      out of the box with no env set.
//
// To use a different backend, start Expo with the variable set, e.g.:
//   PowerShell:  $env:EXPO_PUBLIC_API_URL="https://api.example.com"; npx expo start
//   or put EXPO_PUBLIC_API_URL=... in mobile/.env (see .env.example).

const DEFAULT_API_BASE_URL = "http://192.168.3.19:8000";

function resolveApiBaseUrl(): { url: string; fromEnv: boolean } {
  const raw = process.env.EXPO_PUBLIC_API_URL;
  if (typeof raw === "string" && raw.trim().length > 0) {
    // Drop any trailing slash so `${API_BASE_URL}/sessions` never doubles up.
    return { url: raw.trim().replace(/\/+$/, ""), fromEnv: true };
  }
  return { url: DEFAULT_API_BASE_URL, fromEnv: false };
}

const resolved = resolveApiBaseUrl();

export const API_BASE_URL = resolved.url;

// Whether the value came from configuration or the built-in LAN fallback.
export const API_BASE_URL_IS_DEFAULT = !resolved.fromEnv;

// Surface the active base URL once at startup so it is obvious (in the Metro/dev
// console and device logs) which backend the app is talking to.
console.log(
  `[MultiMic] API base URL: ${API_BASE_URL}` +
    (API_BASE_URL_IS_DEFAULT
      ? " (default LAN fallback)"
      : " (from EXPO_PUBLIC_API_URL)"),
);