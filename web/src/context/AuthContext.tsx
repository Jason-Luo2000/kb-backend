import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { getMe } from "../api";
import type { Me } from "../types";

interface AuthCtx {
  apiKey: string | null;
  login: (key: string, base?: string) => Promise<Me>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>(null!);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [apiKey, setApiKey] = useState<string | null>(() => localStorage.getItem("kb_api_key"));

  const login = useCallback(async (key: string, base?: string) => {
    if (base) api.defaults.baseURL = base.replace(/\/$/, "");
    localStorage.setItem("kb_api_key", key);
    const data = await getMe(); // 失败抛出 → Login 页 catch
    setApiKey(key);
    return data;
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("kb_api_key");
    setApiKey(null);
  }, []);

  return <Ctx.Provider value={{ apiKey, login, logout }}>{children}</Ctx.Provider>;
}
