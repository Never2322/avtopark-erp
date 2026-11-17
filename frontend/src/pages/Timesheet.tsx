// Табель: сколько заработал каждый сотрудник в каждый день месяца. Деньги берутся из раздела «Часы работы и ремонта»;
// отметок (явка, отпуск, больничный) здесь нет. Клик по сумме открывает записи часов этого человека за этот день.
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Plus, Printer } from "lucide-react";
import { get, qs } from "../api";
import { useAuth } from "../auth";
import { useUrlState } from "../hooks";
import { fmtMoney, fmtNum, todayIso } from "../format";
import { Button, Card, Empty, PageHeader, QueryState } from "../ui";

export interface PayCell { work_h: number; repair_h: number; work: number; repair: number; total: number }
export interface SheetRow {
  employee_id: number; name: string; position: string; archived: boolean; days: Record<string, PayCell>; totals: PayCell; worked_days: number;
}
export interface Sheet {
  month: string; title: string; month_gen: string; legal_entity: { id: number; name: string; full_name: string }; entities: { id: number; name: string }[];
  days: { n: number; wd: string; off: boolean; today: boolean }[]; rows: SheetRow[]; day_totals: Record<string, number>; totals: PayCell;
  can_export: boolean; can_add: boolean;
}

export const fmtH = (h: number) => String(h).replace(".", ",");
/** Сумма в клетке дня: целые рубли (копейки видны в подсказке и в итогах). */
export const cellMoney = (v: number) => (v ? fmtNum(Math.round(v), 0) : "");
const payHint = (c: PayCell) => [c.work_h ? `Работа ${fmtH(c.work_h)} ч — ${fmtMoney(c.work)} ₽` : "", c.repair_h ? `Ремонт ${fmtH(c.repair_h)} ч — ${fmtMoney(c.repair)} ₽` : ""].filter(Boolean).join("; ");
export const currentMonth = () => todayIso().slice(0, 7);
export const shiftMonth = (m: string, delta: number) => { const [y, mo] = m.split("-").map(Number); const n = y * 12 + mo - 1 + delta; return `${Math.floor(n / 12)}-${String((n % 12) + 1).padStart(2, "0")}`; };

export function useTimesheetEntity() {
  const { me, can, entityId } = useAuth();
  const allowed = (me?.legal_entities ?? []).filter((e) => can("timesheet.view", e.id) && can("worklog.view", e.id));
  const [s, setS] = useUrlState({ month: "", entity: "" });
  const picked = Number(s.entity) || undefined;
  const entity = entityId && allowed.some((e) => e.id === entityId) ? entityId : picked && allowed.some((e) => e.id === picked) ? picked : allowed[0]?.id;
  return { allowed, entity, byGlobalSwitch: !!entityId && entity === entityId, month: s.month || currentMonth(), setS };
}

export function Timesheet() {
  const { allowed, entity, byGlobalSwitch, month, setS } = useTimesheetEntity();
  const q = useQuery({ queryKey: ["timesheet", entity, month], queryFn: () => get<Sheet>("/timesheet", { month, legal_entity_id: entity }), enabled: !!entity, placeholderData: (p) => p });
  const sheet = q.data;
  const multiEntity = allowed.length > 1 && !byGlobalSwitch;
  const day = (m: string, n: number) => `${m}-${String(n).padStart(2, "0")}`;
  // Месяц длинный: при открытии подвигаем сетку так, чтобы сегодняшний день (или последний день месяца) был на виду.
  const wrap = useRef<HTMLDivElement>(null);
  const loaded = sheet?.month;
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const target = el.querySelector<HTMLElement>("th.ts-day.today") ?? el.querySelector<HTMLElement>("th.ts-day:last-of-type");
    if (!target) return;
    // видимая часть для дней: между закреплённым именем слева и закреплёнными «Часов»/«Итого» справа; ставим нужный день посередине
    const width = (sel: string) => el.querySelector<HTMLElement>(sel)?.offsetWidth ?? 0;
    const left = width("th.ts-name"), right = width("th.ts-hours") * (getComputedStyle(el.querySelector(".ts-hours")!).display === "none" ? 0 : 1) + width("th.ts-total");
    const free = el.clientWidth - left - right;
    el.scrollLeft = Math.max(0, target.offsetLeft + target.offsetWidth / 2 - left - free / 2);
  }, [loaded, entity]);

  return (
    <>
      <PageHeader title="Табель" sub="Сколько заработал каждый сотрудник в каждый день. Суммы берутся из раздела «Часы работы и ремонта»: часы × тариф"
        actions={sheet && (
          <>
            {sheet.can_add && <Link className="btn" to="/worklog"><Plus size={16} aria-hidden /> Внести часы</Link>}
            {sheet.can_export && <a className="btn" href={`/api/timesheet/export.csv${qs({ month, legal_entity_id: entity })}`} download><Download size={16} aria-hidden /> В Excel</a>}
            <Link className="btn" to={`/print/timesheet${qs({ month, entity })}`} target="_blank"><Printer size={16} aria-hidden /> Печать</Link>
          </>
        )} />
      {!entity ? <Empty title="Нет доступа к табелю">Попросите администратора выдать права «Табель: смотреть» и «Часы работы и ремонта: смотреть».</Empty> : (
        <>
          <div className="toolbar">
            <div className="row" style={{ gap: 6 }}>
              <Button icon={ChevronLeft} aria-label="Прошлый месяц" onClick={() => setS({ month: shiftMonth(month, -1) })} />
              <input className="input compact" type="month" aria-label="Месяц" value={month} onChange={(e) => e.target.value && setS({ month: e.target.value })} />
              <Button icon={ChevronRight} aria-label="Следующий месяц" onClick={() => setS({ month: shiftMonth(month, 1) })} />
              {month !== currentMonth() && <Button onClick={() => setS({ month: currentMonth() })}>Этот месяц</Button>}
            </div>
            {multiEntity && (
              <select className="input compact" aria-label="Юридическое лицо" value={entity} onChange={(e) => setS({ entity: e.target.value })}>
                {allowed.map((e) => <option key={e.id} value={e.id}>{e.short_name || e.name}</option>)}
              </select>
            )}
          </div>
          <QueryState q={q}>
            {sheet && (
              <div className="stack">
                <div className="grid cols-3 pay-kpis" aria-label="Заработок за месяц">
                  <Card><span className="muted small">Работа</span><span className="pay-big">{fmtMoney(sheet.totals.work)} ₽</span><span className="muted small">{fmtH(sheet.totals.work_h)} ч</span></Card>
                  <Card><span className="muted small">Ремонт</span><span className="pay-big">{fmtMoney(sheet.totals.repair)} ₽</span><span className="muted small">{fmtH(sheet.totals.repair_h)} ч</span></Card>
                  <Card><span className="muted small">Всего за месяц</span><span className="pay-big">{fmtMoney(sheet.totals.total)} ₽</span><span className="muted small">до вычета налогов</span></Card>
                </div>
                <Card flush title={<>{sheet.title}, {sheet.legal_entity.name}</>}>
                  {sheet.rows.length === 0 ? (
                    <Empty title="В этом месяце в юрлице никто не работает">Сотрудников добавляют в разделе «Сотрудники»: карточка → «Принять в юрлицо».</Empty>
                  ) : (
                    <div className="ts-wrap" ref={wrap}>
                      <table className="ts-table ts-money-table">
                        <thead>
                          <tr>
                            <th className="ts-name">Сотрудник</th>
                            {sheet.days.map((d) => <th key={d.n} className={`ts-day ${d.off ? "off" : ""} ${d.today ? "today" : ""}`}><span>{d.n}</span><small>{d.wd}</small></th>)}
                            <th className="ts-sum ts-hours" title="Отработано часов за месяц">Часов</th><th className="ts-sum ts-total" title="Заработано за месяц, до вычета налогов">Итого, ₽</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sheet.rows.map((r) => (
                            <tr key={r.employee_id} className={r.worked_days === 0 ? "ts-empty-row" : undefined}>
                              <th className="ts-name" scope="row"><strong>{r.name}</strong>{r.position && <small>{r.position}</small>}</th>
                              {sheet.days.map((d) => {
                                const c = r.days[String(d.n)];
                                const iso = day(sheet.month, d.n);
                                return (
                                  <td key={d.n} className={`ts-amt ${d.off ? "off" : ""} ${d.today ? "today" : ""}`}>
                                    {c ? (
                                      <Link to={`/worklog${qs({ q: r.name.split(" ")[0], date_from: iso, date_to: iso })}`} title={`${fmtMoney(c.total)} ₽. ${payHint(c)}`}
                                        aria-label={`${r.name}, ${d.n} число: ${fmtMoney(c.total)} ₽`}>{cellMoney(c.total)}</Link>
                                    ) : null}
                                  </td>
                                );
                              })}
                              <td className="ts-sum ts-hours num">{r.totals.work_h + r.totals.repair_h ? fmtH(Math.round((r.totals.work_h + r.totals.repair_h) * 10) / 10) : ""}</td>
                              <td className="ts-sum ts-total num" title={payHint(r.totals)}><strong>{r.totals.total ? fmtMoney(r.totals.total) : ""}</strong></td>
                            </tr>
                          ))}
                        </tbody>
                        <tfoot>
                          <tr className="ts-pay-row ts-pay-total">
                            <th className="ts-name" scope="row"><strong>Итого за день, ₽</strong></th>
                            {sheet.days.map((d) => { const v = sheet.day_totals[String(d.n)] ?? 0; return <td key={d.n} className={`ts-amt ${d.off ? "off" : ""}`} title={v ? `${fmtMoney(v)} ₽` : undefined}>{cellMoney(v)}</td>; })}
                            <td className="ts-sum ts-hours num">{fmtH(Math.round((sheet.totals.work_h + sheet.totals.repair_h) * 10) / 10)}</td>
                            <td className="ts-sum ts-total num"><strong>{fmtMoney(sheet.totals.total)}</strong></td>
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                  )}
                </Card>
                <p className="muted small" style={{ margin: 0 }}>
                  В клетке дня стоит сумма за этот день; наведите на неё, чтобы увидеть работу и ремонт отдельно, нажмите, чтобы открыть записи часов. Пустая клетка: в этот день часов не внесено.
                  Суммы считаются по тарифам, которые были выбраны при внесении часов. Налоги и вычеты не учитываются. Итог за месяц по сотрудникам есть в отчёте «Зарплата за месяц».
                </p>
              </div>
            )}
          </QueryState>
        </>
      )}
    </>
  );
}
