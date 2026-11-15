// Печатная форма табеля: альбомный лист А4, месяц целиком, расшифровка использованных кодов и подписи.
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Printer } from "lucide-react";
import { get } from "../api";
import { Alert, Button, QueryState } from "../ui";
import { fmtH, type Sheet, useTimesheetEntity } from "./Timesheet";

export function TimesheetPrint() {
  const nav = useNavigate();
  const { entity, month } = useTimesheetEntity();
  const q = useQuery({ queryKey: ["timesheet", entity, month], queryFn: () => get<Sheet>("/timesheet", { month, legal_entity_id: entity }), enabled: !!entity, retry: false });
  const d = q.data;
  useEffect(() => {
    if (d) document.title = `Табель ${d.title}`;
    return () => { document.title = "Автопарк ERP"; };
  }, [d]);

  const usedCodes = d ? d.codes.filter((c) => d.rows.some((r) => Object.values(r.marks).some((m) => m.code === c.code))) : [];
  const total = d ? d.rows.reduce((a, r) => ({ days: a.days + r.days, hours: a.hours + r.hours, absent: a.absent + r.absent }), { days: 0, hours: 0, absent: 0 }) : null;

  return (
    <div className="print-root">
      {/* лист альбомный: общее правило для портретных форм переопределяется только на этой странице */}
      <style>{"@media print { @page { size: A4 landscape; margin: 10mm; } }"}</style>
      <div className="print-bar no-print" style={{ maxWidth: 1123 }}>
        <Button icon={ArrowLeft} onClick={() => (window.history.length > 1 ? nav(-1) : nav("/timesheet"))}>Назад</Button>
        <Button variant="primary" icon={Printer} onClick={() => window.print()} disabled={!d}>Печать</Button>
        <span className="muted small">В окне печати выберите «Альбомная» ориентация, если она не выбралась сама; можно сохранить как PDF.</span>
      </div>
      {!entity ? <Alert tone="error">Нет доступа к табелю.</Alert> : (
        <QueryState q={q}>
          {d && (
            <div className="sheet ts-print">
              <div className="center"><strong>{d.legal_entity.full_name || d.legal_entity.name}</strong></div>
              <h1 className="center" style={{ textTransform: "uppercase" }}>Табель учёта рабочего времени</h1>
              <div className="center">за {d.title.toLowerCase()}</div>
              <table className="ptable ts-ptable">
                <thead>
                  <tr>
                    <th rowSpan={2} style={{ width: 28 }}>№</th>
                    <th rowSpan={2} style={{ textAlign: "left", minWidth: 150 }}>Сотрудник</th>
                    {d.days.map((x) => <th key={x.n} className={x.off ? "off" : ""}>{x.n}</th>)}
                    <th rowSpan={2}>Дней</th><th rowSpan={2}>Часов</th><th rowSpan={2}>Неявок</th>
                  </tr>
                  <tr>{d.days.map((x) => <th key={x.n} className={`wd ${x.off ? "off" : ""}`}>{x.wd}</th>)}</tr>
                </thead>
                <tbody>
                  {d.rows.map((r, i) => (
                    <tr key={r.employee_id}>
                      <td className="c">{i + 1}</td>
                      <td style={{ textAlign: "left" }}>{r.name}{r.position && <div className="cap">{r.position}</div>}</td>
                      {d.days.map((x) => <td key={x.n} className={`c ts-p ${x.off ? "off" : ""}`}>{r.marks[String(x.n)]?.text ?? ""}</td>)}
                      <td className="num">{r.days}</td><td className="num">{fmtH(r.hours)}</td><td className="num">{r.absent}</td>
                    </tr>
                  ))}
                  {total && d.rows.length > 1 && (
                    <tr className="strong">
                      <td colSpan={2 + d.days.length} style={{ textAlign: "right" }}>Итого</td>
                      <td className="num">{total.days}</td><td className="num">{fmtH(Math.round(total.hours * 10) / 10)}</td><td className="num">{total.absent}</td>
                    </tr>
                  )}
                </tbody>
              </table>
              {usedCodes.length > 0 && (
                <p className="cap" style={{ marginTop: 6 }}>
                  Условные обозначения: {usedCodes.map((c) => `${c.code} — ${c.label.toLowerCase()}`).join("; ")}.
                  Явка без указанных часов считается за {d.default_hours} ч.
                </p>
              )}
              <div className="signs three">
                <div className="sign"><span>Ответственный</span><span className="line" /><span className="decode">(должность, подпись, расшифровка)</span></div>
                <div className="sign"><span>Руководитель</span><span className="line" /><span className="decode">(подпись, расшифровка)</span></div>
              </div>
            </div>
          )}
        </QueryState>
      )}
    </div>
  );
}
