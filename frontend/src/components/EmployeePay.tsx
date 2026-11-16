// Блок «Оплата» в карточке сотрудника: какие тарифы действуют для часов работы и часов ремонта.
// Тариф выбирает директор (право «Сотрудники: изменять»); ставка запоминается в каждой записи часов, поэтому смена тарифа прошлое не меняет.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Wallet } from "lucide-react";
import { errorText, get, put } from "../api";
import { fmtMoney } from "../format";
import { Button, QueryState, useToast } from "../ui";

interface PayTariff { id: number; name: string; unit: string; rate: number }
interface Options { work: PayTariff[]; repair: PayTariff[] }
export interface PayInfo { work_tariff_id: number | null; repair_tariff_id: number | null; work_tariff: string | null; repair_tariff: string | null; can_edit: boolean }

const unitText = (u: string) => (u === "day" ? "день" : "час");
const label = (t: PayTariff) => `${t.name} — ${fmtMoney(t.rate)} за ${unitText(t.unit)}`;

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
      </>
    );
  }
  return (
    <>
      {title}
      <QueryState q={opts}>
        <div className="pay-edit">
          <label className="pay-edit-row"><span>Работа</span>{select(work, setWork, opts.data?.work ?? [], "Тариф за работу")}</label>
          <label className="pay-edit-row"><span>Ремонт</span>{select(repair, setRepair, opts.data?.repair ?? [], "Тариф за ремонт")}</label>
          {editable && (
            <div className="row" style={{ gap: 8 }}>
              <Button variant="primary" size="sm" loading={save.isPending} disabled={!dirty} onClick={() => save.mutate()}>Сохранить тарифы</Button>
              <span className="muted small">Новый тариф действует на новые записи часов. Старые записи не пересчитываются.</span>
            </div>
          )}
          {!work && !repair && <p className="muted small">Тариф не выбран: часы записываются, но сумма будет 0, пока тариф не назначат.</p>}
        </div>
      </QueryState>
    </>
  );
}
