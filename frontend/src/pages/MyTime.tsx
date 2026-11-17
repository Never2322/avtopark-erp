// «Мои часы» водителя: сколько часов он работал и ремонтировал и сколько за это начислено.
// Только просмотр: часы вносят директор, диспетчер или механик, сумма считается по тарифу водителя.
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Hammer, Truck } from "lucide-react";
import { get } from "../api";
import { fmtDate, fmtMoney, fmtNum, plural, todayIso } from "../format";
import { Alert, Badge, Button, Card, Empty, PageHeader, QueryState } from "../ui";
import { useUrlState } from "../hooks";

interface TimeItem {
  id: number; work_date: string; kind: "work" | "repair"; kind_label: string; hours: number; vehicle: string; description: string; source: string; office_changed?: boolean;
  rate_text: string; amount: number; no_rate: boolean;
}
interface TimeTariff { id: number; name: string; unit: "hour" | "day"; rate: number }
interface TimeData {
  month: string; title: string; items: TimeItem[];
  totals: { work_h: number; repair_h: number; work: number; repair: number; total: number };
  tariffs: { work: TimeTariff | null; repair: TimeTariff | null };
}

const shiftMonth = (m: string, delta: number) => { const [y, mo] = m.split("-").map(Number); const n = y * 12 + mo - 1 + delta; return `${Math.floor(n / 12)}-${String((n % 12) + 1).padStart(2, "0")}`; };
const tariffText = (t: TimeTariff | null) => (t ? `${t.name}: ${fmtMoney(t.rate)} ₽ ${t.unit === "hour" ? "в час" : "в сутки"}` : "не назначен");
const whoAdded = (s: string) => (s === "mechanic" ? " · внёс механик" : s === "staff" ? " · внесли в офисе" : "");

export function MyTime() {
  const [s, setS] = useUrlState({ month: "" });
  const month = s.month || todayIso().slice(0, 7);
  const q = useQuery({ queryKey: ["my-time", month], queryFn: () => get<TimeData>("/payroll/mine", { month }), placeholderData: (p) => p });
  const d = q.data;
  return (
    <>
      <PageHeader title="Мои часы" sub="Сколько часов вы работали и ремонтировали. Часы вносит директор или механик. Сумма считается по вашему тарифу, а в конце месяца идёт в зарплату" />
      <div className="toolbar">
        <div className="row" style={{ gap: 6 }}>
          <Button icon={ChevronLeft} aria-label="Прошлый месяц" onClick={() => setS({ month: shiftMonth(month, -1) })} />
          <input className="input compact" type="month" aria-label="Месяц" value={month} onChange={(e) => e.target.value && setS({ month: e.target.value })} />
          <Button icon={ChevronRight} aria-label="Следующий месяц" onClick={() => setS({ month: shiftMonth(month, 1) })} />
        </div>
      </div>
      <QueryState q={q}>
        {d && (
          <div className="stack">
            <div className="grid cols-3 pay-kpis">
              <Card><span className="muted small">Работа</span><strong className="pay-big">{fmtNum(d.totals.work_h)} ч</strong><span>{fmtMoney(d.totals.work)} ₽</span></Card>
              <Card><span className="muted small">Ремонт</span><strong className="pay-big">{fmtNum(d.totals.repair_h)} ч</strong><span>{fmtMoney(d.totals.repair)} ₽</span></Card>
              <Card><span className="muted small">Итого за {d.title}</span><strong className="pay-big">{fmtMoney(d.totals.total)} ₽</strong><span className="muted small">до вычета налогов</span></Card>
            </div>
            <p className="muted small">Ваши тарифы. Работа: {tariffText(d.tariffs.work)}. Ремонт: {tariffText(d.tariffs.repair)}.</p>
            {(!d.tariffs.work || !d.tariffs.repair) && <Alert tone="warning">Не назначен тариф: {[!d.tariffs.work && "за работу", !d.tariffs.repair && "за ремонт"].filter(Boolean).join(" и ")}. Пока его нет, часы записываются, а сумма равна нулю. Скажите директору.</Alert>}
            <Card flush title={`Записи за ${d.title}`}>
              {d.items.length === 0 ? <Empty icon={Hammer} title="Записей за этот месяц нет">Часы работы и ремонта вносит директор или механик. Если часов нет, а вы работали, скажите им.</Empty> : (
                <ul className="time-list">
                  {d.items.map((it) => (
                    <li key={it.id} className="time-row">
                      <div className="time-main">
                        <strong>{fmtDate(it.work_date)}</strong>
                        <Badge tone={it.kind === "repair" ? "warning" : "info"}>{it.kind_label}</Badge>
                        {it.office_changed && <Badge>тип изменён в офисе</Badge>}
                        <span>{fmtNum(it.hours)} {plural(Math.round(it.hours), "час", "часа", "часов")}</span>
                        {it.vehicle && <span className="muted small"><Truck size={12} aria-hidden /> {it.vehicle}</span>}
                      </div>
                      {it.description && <div className="muted small">{it.description}</div>}
                      <div className="time-pay">
                        <strong>{it.no_rate ? <Badge tone="error">нет тарифа</Badge> : `${fmtMoney(it.amount)} ₽`}</strong>
                        <span className="muted small">{it.rate_text}{whoAdded(it.source)}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        )}
      </QueryState>
    </>
  );
}
