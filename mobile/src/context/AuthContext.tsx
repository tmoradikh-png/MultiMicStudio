import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api } from "../api/client";
import { clearToken, getToken, setToken } from "../api/client";

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

  useEffect(() => {
    getToken().then((t) => {
      setTokenState(t);
      setLoading(false);
    });
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
