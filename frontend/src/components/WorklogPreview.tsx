// Под формой «Часы работы и ремонта»: сразу видно, сколько денег выйдет по записи и за день у сотрудника.
// Считает сервер (POST /payroll/preview): так суточные тарифы и записи, уже внесённые за день, учитываются так же, как при сохранении.
import { useQuery } from "@tanstack/react-query";
import { Wallet } from "lucide-react";
import { post } from "../api";
import { fmtMoney } from "../format";
import { useDebounced } from "../hooks";
import { Alert } from "../ui";

interface Preview {
  ok: boolean; message?: string; source?: "chosen" | "card" | "none"; tariff_name?: string; rate_text?: string; unit?: "hour" | "day"; rate?: number;
  hours?: number | null; day_hours_other?: number; day_other?: number; amount?: number | null; day_total?: number | null;
}
const fmtH = (h: number) => String(h).replace(".", ",");

export function WorklogPreview({ values, editingId }: { values: Record<string, any>; editingId?: number }) {
  const key = { employee_id: values.employee_id, work_date: values.work_date, kind: values.kind, hours: String(values.hours ?? ""), tariff_id: values.tariff_id };
  const debounced = useDebounced(key, 250);
  const ready = !!debounced.employee_id && !!debounced.work_date;
  const q = useQuery({
    queryKey: ["worklog-preview", debounced, editingId ?? null], enabled: ready, retry: false, placeholderData: (p) => p,
    queryFn: () => post<Preview>("/payroll/preview", {
      employee_id: Number(debounced.employee_id), work_date: debounced.work_date, kind: debounced.kind || "work", hours: debounced.hours,
      tariff_id: debounced.tariff_id ? Number(debounced.tariff_id) : null, exclude_id: editingId ?? null,
    }),
  });
  const d = q.data;
  if (!ready) return <Alert tone="info">Выберите сотрудника: здесь появится сумма за день.</Alert>;
  if (!d) return null;
  if (!d.ok) return <Alert tone="warning">{d.message}</Alert>;
  const noTariff = d.source === "none";
  const perDay = d.unit === "day";
  return (
    <div className="calc-box" aria-live="polite">
      <div className="row" style={{ gap: 8 }}><Wallet size={16} aria-hidden /><strong>Сколько получится</strong></div>
      {noTariff ? (
        <p className="small" style={{ color: "var(--warning)", margin: "6px 0 0" }}>Тариф не выбран, а в карточке сотрудника он не назначен: сумма будет 0. Выберите тариф выше.</p>
      ) : (
        <p className="muted small" style={{ margin: "6px 0 0" }}>
          Тариф: <strong style={{ color: "var(--text)" }}>{d.tariff_name}</strong>, {d.rate_text}{d.source === "card" ? " (из карточки сотрудника)" : ""}
        </p>
      )}
      <div className="calc-rows">
        <div><span className="muted small">Эта запись</span>
          <strong className="calc-sum">{d.amount === null || d.amount === undefined ? "—" : `${fmtMoney(d.amount)} ₽`}</strong>
          {d.amount !== null && d.amount !== undefined && d.hours ? (
            <span className="muted small">{perDay ? (d.amount ? "суточная ставка, один раз за день и вид" : "за этот вид уже начислена суточная ставка") : `${fmtH(d.hours)} ч × ${fmtMoney(d.rate)} ₽`}</span>
          ) : <span className="muted small">впишите часы</span>}
        </div>
        <div><span className="muted small">За день у сотрудника</span>
          <strong className="calc-sum">{d.day_total === null || d.day_total === undefined ? (d.day_other ? `${fmtMoney(d.day_other)} ₽` : "—") : `${fmtMoney(d.day_total)} ₽`}</strong>
          <span className="muted small">{d.day_hours_other ? `уже внесено ${fmtH(d.day_hours_other)} ч на ${fmtMoney(d.day_other ?? 0)} ₽` : "других записей за этот день нет"}</span>
        </div>
      </div>
    </div>
  );
}
