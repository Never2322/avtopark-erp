import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { get, post, setCsrf } from "./api";
import { setTimezone } from "./format";
import { rememberUser } from "./offline";

export interface Me {
  id: number;
  login: string;
  display_name: string;
  must_change_password: boolean;
  employee_id: number | null;
  employee_name: string | null;
  roles: { code: string; name: string; legal_entity_id: number | null }[];
  permissions: Record<string, { all: boolean; entities: number[] }>;
  legal_entities: { id: number; name: string; short_name: string }[];
  csrf_token: string;
  org_timezone: string;
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  sessionExpired: boolean;
  can: (perm: string, entityId?: number | null) => boolean;
  canAny: (...perms: string[]) => boolean;
  entityId: number | null;
  setEntityId: (id: number | null) => void;
  login: (login: string, password: string, remember: boolean) => Promise<Me>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthState>(null as unknown as AuthState);
export const useAuth = () => useContext(Ctx);
const ENTITY_KEY = "erp.entity";

function loadEntity(): number | null {
  try {
    const v = localStorage.getItem(ENTITY_KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [entityId, setEntity] = useState<number | null>(loadEntity());
  const [sessionExpired, setSessionExpired] = useState(false);
  const qc = useQueryClient();

  const apply = useCallback((m: Me | null, fromLogin = false) => {
    setMe(m);
    if (m) {
      setCsrf(m.csrf_token);
      setTimezone(m.org_timezone);
      rememberUser(m.id, fromLogin);                  // другой человек на этом телефоне: сохранённые данные прошлого стираются
    } else {
      setCsrf("");
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await get<Me>("/auth/me"));
    } catch {
      apply(null);
    } finally {
      setLoading(false);
    }
  }, [apply]);

  useEffect(() => {
    refresh();
    const expired = () => {
      setSessionExpired(true);
      apply(null);
      qc.clear();
    };
    window.addEventListener("auth:expired", expired);
    return () => window.removeEventListener("auth:expired", expired);
  }, [refresh, apply, qc]);

  // выбранное юрлицо должно существовать среди доступных: иначе сбрасываем на «все»
  useEffect(() => {
    if (me && entityId !== null && !me.legal_entities.some((e) => e.id === entityId)) setEntity(null);
  }, [me, entityId]);

  const value = useMemo<AuthState>(() => {
    const can = (perm: string, id?: number | null) => {
      const g = me?.permissions[perm];
      if (!g) return false;
      if (id === undefined || id === null) return g.all || g.entities.length > 0;
      return g.all || g.entities.includes(id);
    };
    return {
      me,
      loading,
      sessionExpired,
      can,
      canAny: (...perms) => perms.some((p) => can(p)),
      entityId,
      setEntityId: (id) => {
        setEntity(id);
        try {
          if (id === null) localStorage.removeItem(ENTITY_KEY);
          else localStorage.setItem(ENTITY_KEY, String(id));
        } catch {
          /* хранилище недоступно — переключатель просто не запомнится */
        }
        qc.invalidateQueries();
      },
      login: async (login, password, remember) => {
        const m = await post<Me>("/auth/login", { login, password, remember });
        setSessionExpired(false);
        apply(m, true);
        qc.clear();
        return m;
      },
      logout: async () => {
        try {
          await post("/auth/logout");
        } finally {
          setSessionExpired(false);
          apply(null);
          rememberUser(null, false);                  // выход: сохранённые в телефоне данные стираются
          qc.clear();
        }
      },
      refresh,
    };
  }, [me, loading, sessionExpired, entityId, qc, apply, refresh]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
