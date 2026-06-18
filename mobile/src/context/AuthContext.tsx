import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api } from "../api/client";
import {
  clearActiveSession,
  clearToken,
  getToken,
  setAuthExpiredHandler,
  setToken,
} from "../api/client";

interface AuthContextValue {
  token: string | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, name: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // On launch, load the stored token AND verify it is still valid. A stored but
  // expired JWT used to drop the user on the logged-in Home, where the first real
  // action (e.g. create session) failed with a confusing "access expired" message.
  // We now check it up front and discard it cleanly so the user simply sees Login.
  useEffect(() => {
    let active = true;
    (async () => {
      const t = await getToken();
      if (!t) {
        if (active) setLoading(false);
        return;
      }
      try {
        await api.me();
        if (active) setTokenState(t);
      } catch {
        // Expired/invalid token (or the server is unreachable). If it is clearly
        // an auth failure, drop it; otherwise keep it so an offline start still
        // lets the user back in once the server returns.
        await clearToken();
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // If an account-authenticated request is rejected mid-session (the token
  // expired while the app was open), the API layer calls this to sign the user
  // out cleanly so they return to Login instead of seeing repeated failures.
  useEffect(() => {
    setAuthExpiredHandler(() => {
      clearToken();
      clearActiveSession();
      setTokenState(null);
    });
    return () => setAuthExpiredHandler(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      token,
      loading,
      async signIn(email, password) {
        const res = await api.login(email, password);
        await setToken(res.access_token);
        setTokenState(res.access_token);
      },
      async signUp(email, name, password) {
        const res = await api.signup(email, name, password);
        await setToken(res.access_token);
        setTokenState(res.access_token);
      },
      async signOut() {
        await clearToken();
        await clearActiveSession();
        setTokenState(null);
      },
    }),
    [token, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
