// Кнопки в строках заказов, документов и кассы: печать и «составить документ по заказу».
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FilePlus2, Printer } from "lucide-react";
import { get, post } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Button, Modal, QueryState, useToast } from "../ui";
import { EntryPhotosButton } from "./Entries";
import { ppeActions } from "./ppeActions";
import { maintenanceActions } from "./maintenanceActions";
import type { Row, RowActions } from "./Registry";

const TYPES: { value: string; label: string }[] = [
  { value: "invoice", label: "Счёт на оплату" },
  { value: "act", label: "Акт оказанных услуг" },
  { value: "tn", label: "Транспортная накладная" },
  { value: "upd", label: "УПД" },
];

function PrintLink({ to, label = "Печать" }: { to: string; label?: string }) {
  return <Link className="btn ghost sm" to={to} aria-label={label} title={label}><Printer size={16} aria-hidden /></Link>;
}

function OrderDocs({ order, onClose, onChanged }: { order: Row; onClose: () => void; onChanged: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const mayCreate = can("documents.create", order.legal_entity_id);
  const q = useQuery({ queryKey: ["order-docs", order.id], queryFn: () => get<{ items: Row[] }>("/documents", { order_id: order.id, page_size: 50 }) });
  const make = useMutation({
    mutationFn: (type: string) => post<Row>(`/orders/${order.id}/make-document/${type}`),
    onSuccess: (d) => { toast.success(`Составлен документ: ${d.doc_no}`); q.refetch(); onChanged(); },
    onError: (e: any) => toast.error(e.message),
  });
  const byType = new Map((q.data?.items ?? []).map((d) => [d.doc_type as string, d]));
  return (
    <Modal title={`Документы по заказу ${order.doc_no}`} onClose={onClose} footer={<Button onClick={onClose}>Закрыть</Button>}>
      <QueryState q={q}>
        <div className="stack-sm">
          {!mayCreate && <Alert tone="info">Составлять документы может бухгалтер или директор. Здесь можно только открыть готовые.</Alert>}
          {order.archived && <Alert tone="warning">Заказ в архиве: новые документы не составляются.</Alert>}
          {TYPES.map((t) => {
            const d = byType.get(t.value);
            return (
              <div key={t.value} className="row" style={{ justifyContent: "space-between", gap: 12, padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
                <div>
                  <strong>{t.label}</strong>
                  <div className="muted small">{d ? <>{d.doc_no} · {d.state?.text}</> : "ещё не составлен"}</div>
                </div>
                {d ? (
                  <span className="row" style={{ gap: 8 }}>
                    <Badge tone={d.state?.tone ?? "neutral"}>{fmtTotal(d.total)}</Badge>
                    <Link className="btn sm" to={`/print/documents/${d.id}`}><Printer size={16} aria-hidden /> Печать</Link>
                  </span>
                ) : (
                  <Button size="sm" icon={FilePlus2} disabled={!mayCreate || order.archived} loading={make.isPending && make.variables === t.value} onClick={() => make.mutate(t.value)}>Составить</Button>
                )}
              </div>
            );
          })}
          <p className="muted small">Заказчик, сумма и НДС берутся из заказа. Дату, предмет и цену можно поправить в разделе «Документы».</p>
        </div>
      </QueryState>
    </Modal>
  );
}

const fmtTotal = (v: number) => new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v) + " ₽";

function OrderButtons({ row, done }: { row: Row; done: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <PrintLink to={`/print/orders/${row.id}`} label="Печать заявки" />
      <Button variant="ghost" size="sm" icon={FilePlus2} aria-label="Документы по заказу" title="Документы по заказу" onClick={() => setOpen(true)} />
      {open && <OrderDocs order={row} onClose={() => setOpen(false)} onChanged={done} />}
    </>
  );
}

export const tradeActions = (resource: string): RowActions | undefined => {
  if (resource === "orders") return (r, done) => ({ width: 2, node: <OrderButtons row={r} done={done} /> });
  if (resource === "documents") return (r) => ({ width: 1, node: <PrintLink to={`/print/documents/${r.id}`} label="Печать документа" /> });
  if (resource === "cash") return (r) => (r.kind === "transfer" ? null : { width: 1, node: <PrintLink to={`/print/cash/${r.id}`} label="Печать ордера" /> });
  if (resource === "trips") return (r) => ({ width: 1, node: <EntryPhotosButton kind="trip" id={r.id} count={r.photos} /> });
  if (resource === "fuel_fills") return (r) => ({ width: 1, node: <EntryPhotosButton kind="fuel" id={r.id} count={r.photos} /> });
  if (resource === "ppe") return ppeActions;
  if (resource === "maintenance") return maintenanceActions;
  return undefined;
};
