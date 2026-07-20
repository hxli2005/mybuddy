import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import { fetchCurrentUser, logout as apiLogout, setUnauthorizedHandler } from "./api";

export type AuthState = {
  isLoggedIn: boolean;
  userId: number | null;
  username: string | null;
};

type AuthCtx = AuthState & {
  login: (userId: number, username: string) => void;
  logout: () => void;
  loading: boolean;
};

const AuthContext = createContext<AuthCtx>({
  isLoggedIn: false,
  userId: null,
  username: null,
  login: () => {},
  logout: () => {},
  loading: true,
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ isLoggedIn: false, userId: null, username: null });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // 401 全局处理:清空登录态并跳到登录页
    setUnauthorizedHandler(() => {
      setState({ isLoggedIn: false, userId: null, username: null });
      window.location.hash = "#/login";
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    fetchCurrentUser()
      .then((data) => {
        if (data.user_id) {
          setState({ isLoggedIn: true, userId: data.user_id, username: data.username || null });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback((userId: number, username: string) => {
    setState({ isLoggedIn: true, userId, username });
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      /* ignore */
    }
    setState({ isLoggedIn: false, userId: null, username: null });
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
