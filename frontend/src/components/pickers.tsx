import { useQuery } from "@tanstack/react-query";
import { get } from "../api";
import { Field } from "../ui";

export interface VehicleOption {
  id: number; plate: string; name: string; legal_entity_id: number; internal_no: string; primary_reading: string;
  current_km: number | null; current_hours: number | null;
}
export interface DriverOption { id: number; full_name: string }

export function useVehicleOptions(entityId?: number | null) {
  return useQuery({
    queryKey: ["vehicle-options", entityId ?? null],
    queryFn: () => get<{ items: VehicleOption[] }>("/vehicles/lookup", { legal_entity_id: entityId ?? undefined }),
    staleTime: 30_000,
  });
}

export function useDriverOptions(entityId?: number | null, enabled = true) {
  return useQuery({
    queryKey: ["driver-options", entityId ?? null],
    queryFn: () => get<{ items: DriverOption[] }>("/employees/lookup", { drivers: true, legal_entity_id: entityId ?? undefined }),
    staleTime: 30_000,
    enabled,
    retry: false,
  });
}

/** Выбор машины из доступных пользователю (без архива и списанных). */
export function VehicleSelect({ value, onChange, entityId, error, label = "Машина" }: {
  value: string; onChange: (v: string, opt?: VehicleOption) => void; entityId?: number | null; error?: string; label?: string;
}) {
  const q = useVehicleOptions(entityId);
  return (
    <Field label={label} error={error}>
      <select className="input" value={value} aria-invalid={!!error} onChange={(e) => onChange(e.target.value, q.data?.items.find((v) => String(v.id) === e.target.value))}>
        <option value="">{q.isLoading ? "Загрузка…" : "Выберите машину…"}</option>
        {q.data?.items.map((v) => <option key={v.id} value={v.id}>{v.plate} — {v.name || "без названия"}</option>)}
      </select>
    </Field>
  );
}
