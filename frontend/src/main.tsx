import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "./lib/router";
import { AuthProvider } from "./lib/auth";
import { App } from "./App";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 5000,
    },
  },
});

createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </RouterProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);

