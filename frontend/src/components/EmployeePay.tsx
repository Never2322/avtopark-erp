// Блок «Оплата» в карточке сотрудника: тарифы по умолчанию для часов работы и часов ремонта (подставляются, если тариф не выбран при внесении часов).
// Тариф выбирает директор (право «Сотрудники: изменять»); ставка запоминается в каждой записи часов, поэтому смена тарифа прошлое не меняет.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Wallet } from "lucide-react";
import { errorText, get, put } from "../api";
import { fmtMoney, fmtNum } from "../format";
import { Button, QueryState, useToast } from "../ui";

interface PayTariff { id: number; name: string; unit: string; rate: number }
interface Options { work: PayTariff[]; repair: PayTariff[] }
interface Earned { month: string; title: string; work_h: number; repair_h: number; work: number; repair: number; total: number; no_rate: number; entries: number }
export interface PayInfo { work_tariff_id: number | null; repair_tariff_id: number | null; work_tariff: string | null; repair_tariff: string | null; can_edit: boolean; earned?: Earned }

const unitText = (u: string) => (u === "day" ? "день" : "час");
const label = (t: PayTariff) => `${t.name} — ${fmtMoney(t.rate)} за ${unitText(t.unit)}`;

/** Сколько уже заработано в этом месяце по внесённым часам (то же число видит сам водитель на главной). */
function EarnedLines({ e }: { e: Earned }) {
  return (
    <dl className="kv">
      <dt>Заработано за {e.title}</dt>
      <dd><strong>{fmtMoney(e.total)} ₽</strong> <span className="muted small">до вычета налогов</span></dd>
      <dt>Работа</dt><dd>{fmtNum(e.work_h)} ч · {fmtMoney(e.work)} ₽</dd>
      <dt>Ремонт</dt><dd>{fmtNum(e.repair_h)} ч · {fmtMoney(e.repair)} ₽</dd>
      {e.no_rate > 0 && <><dt>Без тарифа</dt><dd><span style={{ color: "var(--warning)" }}>записей часов: {e.no_rate}</span> <span className="muted small">выберите тариф в записи или назначьте его сотруднику</span></dd></>}
      <dt>Подробно</dt><dd><Link to="/worklog">Часы работы и ремонта</Link></dd>
    </dl>
  );
}

export function EmployeePay({ employeeId, pay, archived }: { employeeId: number; pay: PayInfo; archived: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const editable = pay.can_edit && !archived;
  const opts = useQuery({ queryKey: ["pay-tariffs"], queryFn: () => get<Options>("/payroll/tariffs"), enabled: pay.can_edit });
  const [work, setWork] = useState(pay.work_tariff_id ? String(pay.work_tariff_id) : "");
  const [repair, setRepair] = useState(pay.repair_tariff_id ? String(pay.repair_tariff_id) : "");
  useEffect(() => {
    setWork(pay.work_tariff_id ? String(pay.work_tariff_id) : "");
    setRepair(pay.repair_tariff_id ? String(pay.repair_tariff_id) : "");
  }, [pay.work_tariff_id, pay.repair_tariff_id]);
  const dirty = work !== (pay.work_tariff_id ? String(pay.work_tariff_id) : "") || repair !== (pay.repair_tariff_id ? String(pay.repair_tariff_id) : "");
  const save = useMutation({
    mutationFn: () => put<{ recalculated: number }>(`/payroll/employees/${employeeId}/tariffs`, { work_tariff_id: work ? Number(work) : null, repair_tariff_id: repair ? Number(repair) : null }),
    onSuccess: (r) => {
      qc.invalidateQueries();
      toast.success(r.recalculated ? `Тарифы сохранены. Ставка проставлена в записях часов: ${r.recalculated}` : "Тарифы сохранены");
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const select = (value: string, set: (v: string) => void, list: PayTariff[], aria: string) => (
    <select className="input" aria-label={aria} value={value} disabled={!editable} onChange={(e) => set(e.target.value)}>
      <option value="">Не выбран</option>
      {list.map((t) => <option key={t.id} value={t.id}>{label(t)}</option>)}
      {value && !list.some((t) => String(t.id) === value) && <option value={value}>Тариф в архиве</option>}
    </select>
  );
  const title = <h3 style={{ margin: "6px 0 0", fontSize: 14, display: "flex", alignItems: "center", gap: 6 }}><Wallet size={16} aria-hidden /> Оплата</h3>;
  if (!pay.can_edit) {
    return (
      <>
        {title}
        <dl className="kv">
          <dt>Тариф за работу</dt><dd>{pay.work_tariff || <span className="muted">не выбран</span>}</dd>
          <dt>Тариф за ремонт</dt><dd>{pay.repair_tariff || <span className="muted">не выбран</span>}</dd>
        </dl>
        {pay.earned && <EarnedLines e={pay.earned} />}
      </>
    );
  }
  return (
    <>
      {title}
      {pay.earned && <EarnedLines e={pay.earned} />}
      <QueryState q={opts}>
        <div className="pay-edit">
          <label className="pay-edit-row"><span>Работа</span>{select(work, setWork, opts.data?.work ?? [], "Тариф за работу")}</label>
          <label className="pay-edit-row"><span>Ремонт</span>{select(repair, setRepair, opts.data?.repair ?? [], "Тариф за ремонт")}</label>
          {editable && (
            <div className="row" style={{ gap: 8 }}>
              <Button variant="primary" size="sm" loading={save.isPending} disabled={!dirty} onClick={() => save.mutate()}>Сохранить тарифы</Button>
              <span className="muted small">Эти тарифы подставляются, когда часы вносит механик или тариф не выбран в записи. Новый тариф действует на новые записи, старые не пересчитываются.</span>
            </div>
          )}
          {!work && !repair && <p className="muted small">Тариф по умолчанию не выбран. Когда часы вносит офис, тариф выбирают в самой записи; если часы вносит механик, сумма будет 0, пока тариф не назначат.</p>}
        </div>
      </QueryState>
    </>
  );
}
