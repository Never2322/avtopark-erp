// Печатная форма табеля: альбомный лист А4, сумма заработка за каждый день месяца, итоги и подписи.
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Printer } from "lucide-react";
import { get } from "../api";
import { fmtMoney } from "../format";
import { Alert, Button, QueryState } from "../ui";
import { cellMoney, fmtH, type Sheet, useTimesheetEntity } from "./Timesheet";

export function TimesheetPrint() {
  const nav = useNavigate();
  const { entity, month } = useTimesheetEntity();
  const q = useQuery({ queryKey: ["timesheet", entity, month], queryFn: () => get<Sheet>("/timesheet", { month, legal_entity_id: entity }), enabled: !!entity, retry: false });
  const d = q.data;
  useEffect(() => {
    if (d) document.title = `Табель ${d.title}`;
    return () => { document.title = "Контур"; };
  }, [d]);

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
              <h1 className="center" style={{ textTransform: "uppercase" }}>Табель заработка</h1>
              <div className="center">за {d.title.toLowerCase()}, рублей</div>
              <table className="ptable ts-ptable">
                <thead>
                  <tr>
                    <th rowSpan={2} style={{ width: 28 }}>№</th>
                    <th rowSpan={2} style={{ textAlign: "left", minWidth: 150 }}>Сотрудник</th>
                    {d.days.map((x) => <th key={x.n} className={x.off ? "off" : ""}>{x.n}</th>)}
                    <th rowSpan={2}>Часов</th><th rowSpan={2}>Итого, ₽</th>
                  </tr>
                  <tr>{d.days.map((x) => <th key={x.n} className={`wd ${x.off ? "off" : ""}`}>{x.wd}</th>)}</tr>
                </thead>
                <tbody>
                  {d.rows.map((r, i) => (
                    <tr key={r.employee_id}>
                      <td className="c">{i + 1}</td>
                      <td style={{ textAlign: "left" }}>{r.name}{r.position && <div className="cap">{r.position}</div>}</td>
                      {d.days.map((x) => <td key={x.n} className={`c ts-p ${x.off ? "off" : ""}`}>{cellMoney(r.days[String(x.n)]?.total ?? 0)}</td>)}
                      <td className="num">{r.totals.work_h + r.totals.repair_h ? fmtH(Math.round((r.totals.work_h + r.totals.repair_h) * 10) / 10) : ""}</td>
                      <td className="num">{r.totals.total ? fmtMoney(r.totals.total) : ""}</td>
                    </tr>
                  ))}
                  <tr className="strong">
                    <td colSpan={2} style={{ textAlign: "right" }}>Итого за день</td>
                    {d.days.map((x) => <td key={x.n} className={`c ts-p ${x.off ? "off" : ""}`}>{cellMoney(d.day_totals[String(x.n)] ?? 0)}</td>)}
                    <td className="num">{fmtH(Math.round((d.totals.work_h + d.totals.repair_h) * 10) / 10)}</td><td className="num">{fmtMoney(d.totals.total)}</td>
                  </tr>
                </tbody>
              </table>
              <p className="cap" style={{ marginTop: 6 }}>
                В клетке дня сумма заработка за этот день по часам работы и ремонта (часы × тариф). Суммы до вычета налогов. Пустая клетка: часов за день не внесено.
              </p>
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
