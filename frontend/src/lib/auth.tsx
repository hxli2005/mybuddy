import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import { fetchCurrentUser, logout as apiLogout, setUnauthorizedHandler } from "./api";

export type AuthState = {
  isLoggedIn: boolean;
  userId: number | null;
  username: string | null;
  isAdmin: boolean;
};

type AuthCtx = AuthState & {
  login: (userId: number, username: string, isAdmin?: boolean) => void;
  logout: () => void;
  loading: boolean;
};

const AuthContext = createContext<AuthCtx>({
  isLoggedIn: false,
  userId: null,
  username: null,
  isAdmin: false,
  login: () => {},
  logout: () => {},
  loading: true,
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ isLoggedIn: false, userId: null, username: null, isAdmin: false });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setState({ isLoggedIn: false, userId: null, username: null, isAdmin: false });
      window.location.hash = "#/login";
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    fetchCurrentUser()
      .then((data) => {
        if (data.user_id) {
          setState({ isLoggedIn: true, userId: data.user_id, username: data.username || null, isAdmin: data.role === "admin" });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback((userId: number, username: string, isAdmin: boolean = false) => {
    setState({ isLoggedIn: true, userId, username, isAdmin });
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      /* ignore */
    }
    setState({ isLoggedIn: false, userId: null, username: null, isAdmin: false });
  }, []);

  return (
    <AuthContext.Provider value={{ ...state, login, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
