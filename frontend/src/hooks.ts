import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { get } from "./api";
import { useAuth } from "./auth";

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Состояние страницы хранится в адресной строке: фильтры переживают обновление и ими можно поделиться ссылкой. */
export function useUrlState<T extends Record<string, string>>(defaults: T): [T, (patch: Partial<T>) => void] {
  const [sp, setSp] = useSearchParams();
  const state = useMemo(() => {
    const s = { ...defaults } as Record<string, string>;
    for (const k of Object.keys(defaults)) s[k] = sp.get(k) ?? defaults[k];
    return s as T;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);
  const update = useCallback((patch: Partial<T>) => {
    setSp((old) => {
      const next = new URLSearchParams(old);
      for (const [k, v] of Object.entries(patch)) {
        if (v === undefined || v === defaults[k] || v === "") next.delete(k);
        else next.set(k, String(v));
      }
      // смена фильтра возвращает на первую страницу
      if (!("page" in patch)) next.delete("page");
      return next;
    }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setSp]);
  return [state, update];
}

/** Юрлицо из верхнего переключателя как параметр запроса. */
export function useEntityParam() {
  const { entityId } = useAuth();
  return entityId ?? undefined;
}

export interface VehicleTypes {
  items: { id: number; code: string; name: string; category: string; category_label: string; primary_reading: string }[];
  statuses: { code: string; label: string }[];
  categories: { code: string; label: string }[];
}
export function useVehicleTypes() {
  return useQuery({ queryKey: ["vehicle-types"], queryFn: () => get<VehicleTypes>("/vehicles/types"), staleTime: 10 * 60_000 });
}

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
