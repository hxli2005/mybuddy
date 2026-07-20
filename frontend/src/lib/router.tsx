import { useState, useEffect, createContext, useContext, useCallback, type ReactNode } from "react";

export type Page = "chat" | "login" | "mood" | "assessment";

type RouterCtx = {
  page: Page;
  navigate: (page: Page) => void;
};

const RouterContext = createContext<RouterCtx>({ page: "chat", navigate: () => {} });

function readHash(): Page {
  const hash = (typeof window !== "undefined" ? window.location.hash : "").replace(/^#\/?/, "");
  const valid: Page[] = ["chat", "login", "mood", "assessment"];
  return valid.includes(hash as Page) ? (hash as Page) : "chat";
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [page, setPage] = useState<Page>(readHash);

  useEffect(() => {
    function onHashChange() {
      setPage(readHash());
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = useCallback((p: Page) => {
    if (p === "login") {
      // 记录来源页,登录成功后跳回
      try {
        sessionStorage.setItem("mybuddy-return-page", readHash());
      } catch {
        /* ignore */
      }
    }
    window.location.hash = `#/${p}`;
  }, []);

  return (
    <RouterContext.Provider value={{ page, navigate }}>
      {children}
    </RouterContext.Provider>
  );
}

export function useRouter() {
  return useContext(RouterContext);
}

/** 登录成功后应返回的页面(navigate("login") 时记录)。 */
export function consumeReturnPage(): Page {
  try {
    const stored = sessionStorage.getItem("mybuddy-return-page");
    sessionStorage.removeItem("mybuddy-return-page");
    const valid: Page[] = ["chat", "mood", "assessment"];
    return valid.includes(stored as Page) ? (stored as Page) : "chat";
  } catch {
    return "chat";
  }
}
