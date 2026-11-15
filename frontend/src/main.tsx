import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "./api";
import { AuthProvider } from "./auth";
import { registerOfflineWorker } from "./offline";
import { ToastProvider } from "./ui";
import { App } from "./App";
import "./styles.css";

const client = new QueryClient({
  defaultOptions: {
    // «always»: запрос идёт и без связи — служебный скрипт отдаёт последние сохранённые данные, а не вечную загрузку
    mutations: { networkMode: "always" },
    queries: {
      networkMode: "always",
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => !(err instanceof ApiError && err.status > 0 && err.status < 500) && count < 2,
    },
  },
});

registerOfflineWorker();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <ToastProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
