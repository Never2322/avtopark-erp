// Табель: сетка месяца по сотрудникам юрлица. В клетку пишут код («Я», «Я 6», «Б», «ОТ»), итоги по строке считаются сразу.
// Отметки сохраняются одной кнопкой; быстрое заполнение расставляет выходные, будни или дни по путевым листам.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Printer, Save, Undo2, Wand2 } from "lucide-react";
import { ApiError, get, post, put, qs } from "../api";
import { useAuth } from "../auth";
import { useUrlState } from "../hooks";
import { plural, todayIso } from "../format";
import { Alert, Button, Card, ConfirmModal, Empty, PageHeader, QueryState, useToast } from "../ui";

export interface Code { code: string; label: string; kind: "day" | "absence" | "other"; hours: boolean }
export interface SheetRow {
  employee_id: number; name: string; position: string; archived: boolean;
  marks: Record<string, { code: string; hours: number | null; text: string }>; days: number; hours: number; absent: number;
}
export interface Sheet {
  month: string; title: string; month_gen: string; legal_entity: { id: number; name: string; full_name: string }; entities: { id: number; name: string }[];
  days: { n: number; wd: string; off: boolean; today: boolean }[]; rows: SheetRow[]; codes: Code[]; default_hours: number;
  fill_modes: { value: string; label: string }[]; can_edit: boolean; can_export: boolean;
}

const LOOKALIKE: Record<string, string> = { A: "А", B: "В", C: "С", E: "Е", H: "Н", K: "К", M: "М", O: "О", P: "Р", T: "Т", X: "Х", Y: "У" };
const CELL_RE = /^([A-Za-zА-Яа-яЁё]{1,2})[\s/:]*(\d{1,2}(?:[.,]\d+)?)?$/;

export type Parsed = { ok: true; code: string | null; hours: number | null } | { ok: false; error: string };

/** То же разбор клетки, что и на сервере: нужен, чтобы показывать итоги и ошибки сразу; окончательно решает сервер. */
export function parseCell(raw: string, codes: Code[]): Parsed {
  const t = raw.trim();
  if (!t) return { ok: true, code: null, hours: null };
  const m = CELL_RE.exec(t);
  if (!m) return { ok: false, error: "Нужен код, например Я, Б или «Я 6»" };
  const code = m[1].toUpperCase().split("").map((c) => LOOKALIKE[c] ?? c).join("");
  const meta = codes.find((c) => c.code === code);
  if (!meta) return { ok: false, error: `Такого кода нет: ${m[1]}` };
  let hours: number | null = null;
  if (m[2]) {
    hours = Number(m[2].replace(",", "."));
    if (Math.round(hours * 10) / 10 !== hours) return { ok: false, error: "Часы с точностью до десятых" };
    if (hours > 24) return { ok: false, error: "В сутках не больше 24 часов" };
    if (!meta.hours) return { ok: false, error: `У кода ${code} часы не указывают` };
  }
  if (code === "С" && hours === null) return { ok: false, error: "Для «С» укажите часы, например «С 2»" };
  return { ok: true, code, hours };
}

export function sums(cells: { code: string | null; hours: number | null }[], codes: Code[], defHours: number) {
  let days = 0, hours = 0, absent = 0;
  for (const c of cells) {
    if (!c.code) continue;
    const kind = codes.find((x) => x.code === c.code)?.kind;
    if (kind === "day") { days++; hours += c.hours ?? defHours; }
    else if (c.code === "С") hours += c.hours ?? 0;
    else if (kind === "absence") absent++;
  }
  return { days, hours: Math.round(hours * 10) / 10, absent };
}

export const fmtH = (h: number) => String(h).replace(".", ",");
export const currentMonth = () => todayIso().slice(0, 7);
export const shiftMonth = (m: string, delta: number) => { const [y, mo] = m.split("-").map(Number); const n = y * 12 + mo - 1 + delta; return `${Math.floor(n / 12)}-${String((n % 12) + 1).padStart(2, "0")}`; };

export function useTimesheetEntity() {
  const { me, can, entityId } = useAuth();
  const allowed = (me?.legal_entities ?? []).filter((e) => can("timesheet.view", e.id));
  const [s, setS] = useUrlState({ month: "", entity: "" });
  const picked = Number(s.entity) || undefined;
  const entity = entityId && allowed.some((e) => e.id === entityId) ? entityId : picked && allowed.some((e) => e.id === picked) ? picked : allowed[0]?.id;
  return { allowed, entity, byGlobalSwitch: !!entityId && entity === entityId, month: s.month || currentMonth(), setS };
}

const key = (eid: number, d: number) => `${eid}:${d}`;

export function Timesheet() {
  const { allowed, entity, byGlobalSwitch, month, setS } = useTimesheetEntity();
  const qc = useQueryClient();
  const toast = useToast();
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [bad, setBad] = useState<Record<string, string>>({});
  const [focus, setFocus] = useState<string | null>(null);
  const [mode, setMode] = useState("weekdays");
  const [pendingNav, setPendingNav] = useState<null | { month?: string; entity?: string }>(null);
  const grid = useRef<HTMLDivElement>(null);
  const qkey = ["timesheet", entity, month];
  const q = useQuery({ queryKey: qkey, queryFn: () => get<Sheet>("/timesheet", { month, legal_entity_id: entity }), enabled: !!entity, placeholderData: (p) => p });
  const sheet = q.data;
  const dirty = Object.keys(edits).length;

  useEffect(() => { setEdits({}); setBad({}); }, [entity, month]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const valueOf = (r: SheetRow, d: number) => edits[key(r.employee_id, d)] ?? r.marks[String(d)]?.text ?? "";

  const live = useMemo(() => {
    if (!sheet) return new Map<number, { days: number; hours: number; absent: number }>();
    const out = new Map<number, { days: number; hours: number; absent: number }>();
    for (const r of sheet.rows) {
      const cells = sheet.days.map((day) => {
        const p = parseCell(edits[key(r.employee_id, day.n)] ?? r.marks[String(day.n)]?.text ?? "", sheet.codes);
        return p.ok ? p : { code: null, hours: null };
      });
      out.set(r.employee_id, sums(cells, sheet.codes, sheet.default_hours));
    }
    return out;
  }, [sheet, edits]);

  const save = useMutation({
    mutationFn: () => {
      const cells = Object.entries(edits).map(([k, text]) => { const [eid, day] = k.split(":").map(Number); return { employee_id: eid, day, text }; });
      return put<Sheet & { changed: number }>("/timesheet/cells", { legal_entity_id: entity, month, cells });
    },
    onSuccess: (data) => { qc.setQueryData(qkey, data); setEdits({}); setBad({}); toast.success(data.changed ? `Сохранено: ${data.changed} ${plural(data.changed, "отметка", "отметки", "отметок")}` : "Изменений не было"); },
    onError: (e) => {
      if (e instanceof ApiError && e.fields) setBad(e.fields);
      else toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить");
    },
  });
  const fill = useMutation({
    mutationFn: () => post<Sheet & { added: number }>("/timesheet/fill", { legal_entity_id: entity, month, mode }),
    onSuccess: (data) => { qc.setQueryData(qkey, data); toast.success(data.added ? `Проставлено отметок: ${data.added}. Заполненные дни не менялись` : "Нечего проставлять: подходящие дни уже заполнены"); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось заполнить"),
  });

  const go = (patch: { month?: string; entity?: string }) => (dirty ? setPendingNav(patch) : setS(patch));

  const setCell = (eid: number, d: number, text: string) => {
    const k = key(eid, d);
    setEdits((e) => ({ ...e, [k]: text }));
    setBad((b) => { const nk = `c_${eid}_${d}`; if (!(nk in b)) return b; const { [nk]: _x, ...rest } = b; return rest; });
  };
  const move = (r: number, d: number, dr: number, dd: number) => {
    const el = grid.current?.querySelector<HTMLInputElement>(`input[data-r="${r + dr}"][data-d="${d + dd}"]`);
    if (el) { el.focus(); el.select(); }
  };
  const pickCode = (code: string) => {
    if (!focus || !sheet?.can_edit) { toast.error("Сначала выберите клетку в таблице"); return; }
    const [eid, d] = focus.split(":").map(Number);
    setCell(eid, d, code);
  };

  const used = sheet ? sheet.codes : [];
  const nothing = !entity;
  const multiEntity = allowed.length > 1 && !byGlobalSwitch;

  return (
    <>
      <PageHeader title="Табель" sub="Учёт рабочего времени по сотрудникам. В клетку пишут код: Я — явка, Б — больничный, ОТ — отпуск, В — выходной; после кода можно часы: «Я 6»"
        actions={sheet && (
          <>
            {sheet.can_export && <a className="btn" href={`/api/timesheet/export.csv${qs({ month, legal_entity_id: entity })}`} download><Download size={16} aria-hidden /> В Excel</a>}
            <Link className="btn" to={`/print/timesheet${qs({ month, entity })}`} target="_blank"><Printer size={16} aria-hidden /> Печать</Link>
          </>
        )} />
      {nothing ? <Empty title="Нет доступа к табелю">Попросите администратора выдать право «Табель».</Empty> : (
        <>
          <div className="toolbar">
            <div className="row" style={{ gap: 6 }}>
              <Button icon={ChevronLeft} aria-label="Прошлый месяц" onClick={() => go({ month: shiftMonth(month, -1) })} />
              <input className="input compact" type="month" aria-label="Месяц" value={month} onChange={(e) => e.target.value && go({ month: e.target.value })} />
              <Button icon={ChevronRight} aria-label="Следующий месяц" onClick={() => go({ month: shiftMonth(month, 1) })} />
              {month !== currentMonth() && <Button onClick={() => go({ month: currentMonth() })}>Этот месяц</Button>}
            </div>
            {multiEntity && (
              <select className="input compact" aria-label="Юридическое лицо" value={entity} onChange={(e) => go({ entity: e.target.value })}>
                {allowed.map((e) => <option key={e.id} value={e.id}>{e.short_name || e.name}</option>)}
              </select>
            )}
            <span className="spacer" />
            {sheet?.can_edit && (
              <>
                <select className="input compact" style={{ maxWidth: 210, minWidth: 0 }} aria-label="Чем заполнить" value={mode} onChange={(e) => setMode(e.target.value)} disabled={dirty > 0}>
                  {sheet.fill_modes.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
                <Button icon={Wand2} loading={fill.isPending} disabled={dirty > 0} title={dirty ? "Сначала сохраните отметки" : "Расставить пустые дни. Заполненные не меняются"} onClick={() => fill.mutate()}>Заполнить</Button>
                <Button icon={Undo2} disabled={!dirty} onClick={() => { setEdits({}); setBad({}); }}>Отменить</Button>
                <Button variant="primary" icon={Save} loading={save.isPending} disabled={!dirty} onClick={() => save.mutate()}>Сохранить{dirty ? ` (${dirty})` : ""}</Button>
              </>
            )}
          </div>
          <QueryState q={q}>
            {sheet && (
              <div className="stack">
                {Object.keys(bad).length > 0 && <Alert tone="error">Не сохранено, проверьте клетки: {Object.values(bad).join("; ")}</Alert>}
                {!sheet.can_edit && <Alert tone="info">У вас право только смотреть табель.</Alert>}
                <Card flush title={<>{sheet.title}, {sheet.legal_entity.name}</>}>
                  {sheet.rows.length === 0 ? (
                    <Empty title="В этом месяце в юрлице никто не работает">Сотрудников добавляют в разделе «Сотрудники»: карточка → «Принять в юрлицо».</Empty>
                  ) : (
                    <div className="ts-wrap" ref={grid}>
                      <table className="ts-table">
                        <thead>
                          <tr>
                            <th className="ts-name">Сотрудник</th>
                            {sheet.days.map((d) => <th key={d.n} className={`ts-day ${d.off ? "off" : ""} ${d.today ? "today" : ""}`}><span>{d.n}</span><small>{d.wd}</small></th>)}
                            <th className="ts-sum" title="Дней явки">Дней</th><th className="ts-sum" title="Отработано часов">Часов</th><th className="ts-sum" title="Дней неявок">Неявок</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sheet.rows.map((r, ri) => {
                            const t = live.get(r.employee_id) ?? { days: r.days, hours: r.hours, absent: r.absent };
                            return (
                              <tr key={r.employee_id}>
                                <th className="ts-name" scope="row"><strong>{r.name}</strong>{r.position && <small>{r.position}</small>}</th>
                                {sheet.days.map((d) => {
                                  const v = valueOf(r, d.n);
                                  const p = parseCell(v, sheet.codes);
                                  const k = key(r.employee_id, d.n);
                                  const serverBad = bad[`c_${r.employee_id}_${d.n}`];
                                  const kind = !p.ok || serverBad ? "bad" : p.code ? sheet.codes.find((c) => c.code === p.code)?.kind ?? "other" : "";
                                  return (
                                    <td key={d.n} className={`ts-td ${d.off ? "off" : ""} ${d.today ? "today" : ""}`}>
                                      <input className={`ts-cell ${kind} ${k in edits ? "edited" : ""}`} value={v} readOnly={!sheet.can_edit} maxLength={8} autoComplete="off" spellCheck={false}
                                        data-r={ri} data-d={d.n} aria-label={`${r.name}, ${d.n} число`} aria-invalid={kind === "bad"} title={!p.ok ? p.error : serverBad || (p.ok && p.code ? sheet.codes.find((c) => c.code === p.code)?.label : undefined)}
                                        onFocus={(e) => { setFocus(k); e.currentTarget.select(); }}
                                        onChange={(e) => setCell(r.employee_id, d.n, e.target.value)}
                                        onKeyDown={(e) => {
                                          if (e.key === "ArrowDown" || e.key === "Enter") { e.preventDefault(); move(ri, d.n, 1, 0); }
                                          else if (e.key === "ArrowUp") { e.preventDefault(); move(ri, d.n, -1, 0); }
                                          else if (e.key === "ArrowRight" && e.currentTarget.selectionStart === e.currentTarget.value.length) { e.preventDefault(); move(ri, d.n, 0, 1); }
                                          else if (e.key === "ArrowLeft" && e.currentTarget.selectionEnd === 0) { e.preventDefault(); move(ri, d.n, 0, -1); }
                                        }} />
                                    </td>
                                  );
                                })}
                                <td className="ts-sum num">{t.days}</td><td className="ts-sum num">{fmtH(t.hours)}</td><td className="ts-sum num">{t.absent}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </Card>
                <Card title="Коды" className="ts-legend-card">
                  <div className="ts-legend">
                    {used.map((c) => (
                      <button key={c.code} type="button" className={`ts-chip ${c.kind}`} disabled={!sheet.can_edit} onMouseDown={(e) => e.preventDefault()} onClick={() => pickCode(c.code)} title={sheet.can_edit ? "Поставить в выбранную клетку" : undefined}>
                        <b>{c.code}</b> {c.label}
                      </button>
                    ))}
                  </div>
                  <p className="muted small" style={{ marginTop: 10 }}>
                    Явка («Я», «Н», «РВ», «К») без часов считается за {sheet.default_hours} ч. Часы пишут через пробел: «Я 6», «С 2». Стрелки и Enter переходят между клетками.
                    Праздничные дни программа не знает: отметьте их вручную. Пустая клетка — день не отмечен.
                  </p>
                </Card>
              </div>
            )}
          </QueryState>
        </>
      )}
      {pendingNav && (
        <ConfirmModal title="Есть несохранённые отметки" danger confirmLabel="Уйти без сохранения" reasonRequired={false}
          text={<>Если перейти, {dirty} {plural(dirty, "изменённая клетка пропадёт", "изменённые клетки пропадут", "изменённых клеток пропадут")}. Сначала нажмите «Сохранить», если они нужны.</>}
          onConfirm={async () => { setEdits({}); setBad({}); setS(pendingNav); }} onClose={() => setPendingNav(null)} />
      )}
    </>
  );
}
