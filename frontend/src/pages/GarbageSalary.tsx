// «Зарплата мусоровозов»: две таблицы на месяц. «По дням» — каждый мусоровоз и его экипаж (водитель и грузчики), тонны и ставка
// за тонну, которую можно проставить прямо в строке; за день = тонны × ставка. «Итог за месяц» собирается сам из первой таблицы.
import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronLeft, ChevronRight, Sigma, Wallet } from "lucide-react";
import { get, post, put, errorText } from "../api";
import { fmtDate, fmtMoney, fmtNum, todayIso } from "../format";
import { useEntityParam, useUrlState } from "../hooks";
import { Alert, Badge, Button, Card, Empty, PageHeader, QueryState, useToast } from "../ui";

interface Person { employee_id: number; name: string; role: "driver" | "loader"; trips: number; tons: number; pending: number; rate: number | null; rate_source: "day" | "tariff" | ""; tariff: string; sum: number }
interface Day { date: string; vehicle_id: number; vehicle: string; legal_entity_id: number; trips: number; tons: number; pending: number; people: Person[]; sum: number }
interface MonthRow { employee_id: number; name: string; roles: ("driver" | "loader")[]; days: number; trips: number; tons: number; sum: number; no_rate: number }
interface Data { days: Day[]; month: MonthRow[]; total: number; tons: number; trips: number; pending: number; month_key: string; title: string; can_edit: boolean }

const ROLE = { driver: "Водитель", loader: "Грузчик" } as const;
const shiftMonth = (m: string, delta: number) => { const [y, mo] = m.split("-").map(Number); const n = y * 12 + mo - 1 + delta; return `${Math.floor(n / 12)}-${String((n % 12) + 1).padStart(2, "0")}`; };
const fmtRate = (n: number | null) => (n == null ? "" : String(n).replace(".", ","));

/** Ставка за тонну в строке: сохраняется, когда уходите из поля или жмёте Enter. Пусто — снова по тарифу сотрудника. */
function RateCell({ day, p, can, onSave }: { day: Day; p: Person; can: boolean; onSave: (rate: string) => Promise<unknown> }) {
  const [v, setV] = useState<string | null>(null);
  const shown = v ?? (p.rate_source === "day" ? fmtRate(p.rate) : "");
  const save = async () => {
    if (v === null) return;
    const before = p.rate_source === "day" ? fmtRate(p.rate) : "";
    if (v.trim() === before) { setV(null); return; }
    try { await onSave(v.trim()); } finally { setV(null); }
  };
  if (!can) return <span>{p.rate != null ? fmtMoney(p.rate) : <span className="muted">—</span>}</span>;
  return (
    <input className="input compact gs-rate" inputMode="decimal" value={shown} placeholder={p.rate_source === "tariff" ? fmtRate(p.rate) : "ставка"}
      title={p.rate_source === "tariff" ? `По тарифу сотрудника: ${p.tariff}. Впишите другую ставку, если на этот день она своя` : p.rate_source === "day" ? "Ставка проставлена на этот день. Сотрите, чтобы считать по тарифу" : "Тарифа «за тонну» нет: впишите ставку"}
      aria-label={`Ставка за тонну: ${p.name}, ${fmtDate(day.date)}`}
      onChange={(e) => setV(e.target.value)} onBlur={save} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") setV(null); }} />
  );
}

export function GarbageSalary() {
  const qc = useQueryClient();
  const toast = useToast();
  const entity = useEntityParam();
  const [s, setS] = useUrlState({ month: "" });
  const month = s.month || todayIso().slice(0, 7);
  const key = ["garbage-salary", month, entity];
  const q = useQuery({ queryKey: key, queryFn: () => get<Data>("/garbage-salary", { month, legal_entity_id: entity }), placeholderData: (p) => p });
  const d = q.data;
  const [bulk, setBulk] = useState({ role: "loader", rate: "" });

  const saveRate = (day: Day, p: Person, rate: string) =>
    put<Data>(`/garbage-salary/rate${entity ? `?legal_entity_id=${entity}` : ""}`, { date: day.date, vehicle_id: day.vehicle_id, employee_id: p.employee_id, rate: rate || null })
      .then((r) => qc.setQueryData(key, { ...d, ...r }))
      .catch((e) => toast.error(errorText(e)));
  const fill = useMutation({
    mutationFn: () => post<Data & { changed: number }>(`/garbage-salary/rates${entity ? `?legal_entity_id=${entity}` : ""}`, { month, role: bulk.role, rate: bulk.rate, only_empty: true }),
    onSuccess: (r) => { qc.setQueryData(key, { ...d, ...r }); toast.success(r.changed ? `Ставка проставлена в ${r.changed} строк` : "Пустых ставок не нашлось"); setBulk((b) => ({ ...b, rate: "" })); },
    onError: (e) => toast.error(errorText(e)),
  });
  const noRate = d?.days.reduce((n, x) => n + x.people.filter((p) => p.rate == null && p.tons > 0).length, 0) ?? 0;

  return (
    <>
      <PageHeader title="Зарплата мусоровозов" sub="Заработок экипажа за каждый день: тонны × ставка за тонну. В конце месяца всё складывается в итог по каждому сотруднику" />
      <div className="toolbar">
        <div className="row" style={{ gap: 6 }}>
          <Button icon={ChevronLeft} aria-label="Прошлый месяц" onClick={() => setS({ month: shiftMonth(month, -1) })} />
          <input className="input compact" type="month" aria-label="Месяц" value={month} onChange={(e) => e.target.value && setS({ month: e.target.value })} />
          <Button icon={ChevronRight} aria-label="Следующий месяц" onClick={() => setS({ month: shiftMonth(month, 1) })} />
        </div>
        {d?.can_edit && d.days.length > 0 && (
          <form className="row gs-bulk" style={{ gap: 6 }} onSubmit={(e) => { e.preventDefault(); if (bulk.rate.trim()) fill.mutate(); }}>
            <span className="muted small">Проставить ставку</span>
            <select className="input compact" aria-label="Кому" value={bulk.role} onChange={(e) => setBulk({ ...bulk, role: e.target.value })}>
              <option value="loader">грузчикам</option><option value="driver">водителям</option><option value="all">всем</option>
            </select>
            <input className="input compact gs-rate" inputMode="decimal" placeholder="₽ за тонну" aria-label="Ставка за тонну" value={bulk.rate} onChange={(e) => setBulk({ ...bulk, rate: e.target.value })} />
            <Button type="submit" loading={fill.isPending} disabled={!bulk.rate.trim()} title="Только в строках, где ставки ещё нет">Где пусто</Button>
          </form>
        )}
      </div>
      <QueryState q={q}>
        {d && (d.days.length === 0 ? (
          <Card><Empty icon={Wallet} title={`За ${d.title.toLowerCase()} талонов весов нет`}>Таблица заполнится, когда водители мусоровозов приложат талоны во вкладке «Тоннаж». Грузчики попадут в экипаж, если закреплены за машиной в «Бригадах и замечаниях».</Empty></Card>
        ) : (
          <div className="stack">
            {(noRate > 0 || d.pending > 0) && (
              <Alert tone="warning">
                {noRate > 0 && <>В {noRate} строках нет ставки: там 0 ₽. Впишите ставку в строке или нажмите «Где пусто». </>}
                {d.pending > 0 && <>{d.pending} талонов ещё без веса: они не оплачены, пока вес не внесут.</>}
              </Alert>
            )}
            <Card flush title="По дням" icon={CalendarDays}>
              <div className="table-wrap">
                <table className="table gs-table gs-days">
                  <thead><tr>
                    <th>День</th><th>Машина</th><th>Сотрудник</th><th className="num">Ходок</th><th className="num">Тонн</th>
                    <th className="num">Ставка, ₽/т</th><th className="num">За день, ₽</th>
                  </tr></thead>
                  <tbody>
                    {d.days.map((day) => (
                      <Fragment key={`${day.date}-${day.vehicle_id}`}>
                        {day.people.map((p, i) => (
                          <tr key={p.employee_id} className={i === 0 ? "gs-first" : ""}>
                            {i === 0 && <td rowSpan={day.people.length} className="nowrap"><strong>{fmtDate(day.date)}</strong></td>}
                            {i === 0 && <td rowSpan={day.people.length}>{day.vehicle}{day.pending > 0 && <div className="muted small">{day.pending} без веса</div>}</td>}
                            <td><span className="gs-name">{p.name}</span> <Badge tone={p.role === "driver" ? "info" : "neutral"}>{ROLE[p.role]}</Badge></td>
                            <td className="num">{p.trips}</td>
                            <td className="num">{fmtNum(p.tons, 2)}</td>
                            <td className="num"><RateCell day={day} p={p} can={d.can_edit} onSave={(r) => saveRate(day, p, r)} /></td>
                            <td className="num">{p.rate == null ? <span className="muted">—</span> : <strong>{fmtMoney(p.sum)}</strong>}</td>
                          </tr>
                        ))}
                      </Fragment>
                    ))}
                  </tbody>
                  <tfoot><tr>
                    <td colSpan={3}><strong>Итого за месяц</strong></td><td className="num"><strong>{d.trips}</strong></td>
                    <td className="num"><strong>{fmtNum(d.tons, 2)}</strong></td><td /><td className="num"><strong>{fmtMoney(d.total)}</strong></td>
                  </tr></tfoot>
                </table>
              </div>
            </Card>
            <Card flush title={`Итог за ${d.title.toLowerCase()}`} icon={Sigma} actions={<span className="muted small">считается сам из таблицы «По дням»</span>}>
              <div className="table-wrap">
                <table className="table gs-table">
                  <thead><tr><th>Сотрудник</th><th>Роль</th><th className="num">Дней</th><th className="num">Ходок</th><th className="num">Тонн</th><th className="num">Начислено, ₽</th></tr></thead>
                  <tbody>
                    {d.month.map((m) => (
                      <tr key={m.employee_id}>
                        <td><strong>{m.name}</strong>{m.no_rate > 0 && <div className="muted small">без ставки дней: {m.no_rate}</div>}</td>
                        <td>{m.roles.map((r) => ROLE[r]).join(", ")}</td>
                        <td className="num">{m.days}</td><td className="num">{m.trips}</td><td className="num">{fmtNum(m.tons, 2)}</td>
                        <td className="num"><strong>{fmtMoney(m.sum)}</strong></td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot><tr><td colSpan={5}><strong>Всего</strong></td><td className="num"><strong>{fmtMoney(d.total)}</strong></td></tr></tfoot>
                </table>
              </div>
            </Card>
          </div>
        ))}
      </QueryState>
    </>
  );
}
