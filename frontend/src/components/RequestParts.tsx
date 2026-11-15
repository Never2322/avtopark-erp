// Блоки обращения: «Детали для ремонта» и «Счета по обращению».
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FilePlus2, History, Package, Plus, ReceiptText, ShoppingCart, Trash2, Upload } from "lucide-react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { fmtDate, fmtMoney } from "../format";
import { Badge, Button, Card, ConfirmModal, Modal, useForm, useToast } from "../ui";
import { emptyPart, filledParts, PartsEditor, type PartRow } from "./PartsEditor";

export interface PartItem {
  id: number; name: string; article: string; qty: number; unit: string; qty_text: string; added_at: string; added_by: string | null;
  bought: boolean; bought_at: string | null; bought_by: string | null; hint: string; hint_open: boolean; can_remove: boolean;
}
export interface PartsBlock { items: PartItem[]; total: number; bought: number; to_buy: number; can_add: boolean; max: number }
export interface BillsBlock {
  visible: boolean; total: number; can_create: boolean;
  items: { id: number; number: string; bill_date: string; supplier_name: string; total: number; status: string; status_label: string; kind_label: string }[];
}

function AddParts({ requestId, onClose }: { requestId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [rows, setRows] = useState<PartRow[]>([emptyPart()]);
  const f = useForm({});
  const m = useMutation({
    mutationFn: () => {
      const items = filledParts(rows).map(({ name, article, qty, unit }) => ({ name, article, qty, unit }));
      if (!items.length) throw new ApiError(422, "empty", "Впишите хотя бы одну деталь", "items");
      return api("POST", `/requests/${requestId}/parts`, { items });
    },
    onSuccess: () => { qc.invalidateQueries(); toast.success("Детали добавлены"); onClose(); },
    onError: f.fail,
  });
  const err = f.errors.items ?? f.errors.name ?? f.errors.qty ?? f.errors.unit ?? f.errors.article ?? (f.formError || undefined);
  return (
    <Modal title="Добавить детали" wide onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Добавить в список</Button></>}>
      <PartsEditor rows={rows} onChange={setRows} errors={err} hint="Начните вводить название: программа подскажет детали из каталога. Количество можно дробное, например 4,5 л." />
    </Modal>
  );
}

export function RequestParts({ requestId, parts, open }: { requestId: number; parts: PartsBlock; open: boolean }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<PartItem | null>(null);
  const rm = useMutation({
    mutationFn: (id: number) => api("DELETE", `/requests/${requestId}/parts/${id}`),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Деталь убрана из списка"); },
    onError: (e: any) => toast.error(e.message),
  });
  if (!parts.items.length && !parts.can_add) return null;
  return (
    <Card title={<span className="row wrap" style={{ gap: 10 }}>Детали для ремонта {parts.total > 0 && <Badge tone={parts.to_buy ? "warning" : "success"} icon={parts.to_buy ? ShoppingCart : CheckCircle2}>{parts.to_buy ? `купить: ${parts.to_buy} из ${parts.total}` : "всё куплено"}</Badge>}</span>}
      actions={<>
        {can("purchases.view") && parts.to_buy > 0 && <Link className="btn sm" to="/purchases"><ShoppingCart size={16} aria-hidden /> Что купить</Link>}
        {parts.can_add && <Button size="sm" icon={Plus} onClick={() => setAdding(true)}>Добавить</Button>}
      </>}>
      {parts.items.length === 0 ? (
        <div className="muted">Список пуст. {parts.can_add && "Добавьте детали, которые нужно купить: механик увидит их в разделе «Что купить»."}</div>
      ) : (
        <ul className="parts-list">
          {parts.items.map((x) => (
            <li key={x.id} className={x.bought ? "bought" : ""}>
              <div className="grow">
                <div className="row wrap" style={{ gap: 8 }}>
                  <strong>{x.name}</strong>
                  {x.article && <span className="muted small">арт. {x.article}</span>}
                </div>
                <div className="muted small">
                  {x.qty_text} {x.unit} · добавил(а) {x.added_by ?? "система"}, {fmtDate(x.added_at)}
                  {x.bought && <> · куплено {fmtDate(x.bought_at)}{x.bought_by ? `, ${x.bought_by}` : ""}</>}
                </div>
                {x.hint && <div className={`part-hint ${x.hint_open ? "open" : ""}`}><History size={13} aria-hidden /> {x.hint}</div>}
              </div>
              {x.bought ? <Badge tone="success" icon={CheckCircle2}>куплено</Badge> : <Badge tone="warning" icon={Package}>купить</Badge>}
              {x.can_remove && <Button variant="ghost" size="sm" icon={Trash2} aria-label={`Убрать «${x.name}» из списка`} onClick={() => setRemoving(x)} />}
            </li>
          ))}
        </ul>
      )}
      {!open && parts.items.length > 0 && <div className="muted small" style={{ marginTop: 8 }}>Обращение выполнено или закрыто: список деталей уже не меняется.</div>}
      {adding && <AddParts requestId={requestId} onClose={() => setAdding(false)} />}
      {removing && (
        <ConfirmModal title="Убрать деталь из списка?" danger confirmLabel="Убрать" text={<>«{removing.name}» — {removing.qty_text} {removing.unit}. Её можно будет добавить заново.</>}
          onConfirm={() => rm.mutateAsync(removing.id)} onClose={() => setRemoving(null)} />
      )}
    </Card>
  );
}

export function RequestBills({ requestId, bills }: { requestId: number; bills: BillsBlock }) {
  const nav = useNavigate();
  if (!bills.visible) return null;
  return (
    <Card title="Счета по обращению"
      actions={bills.can_create && <div className="row wrap">
        <Button size="sm" icon={FilePlus2} onClick={() => nav(`/bills/new?kind=issued&request=${requestId}`)}>Выписать счёт</Button>
        <Button size="sm" icon={Upload} onClick={() => nav(`/bills/new?kind=uploaded&request=${requestId}`)}>Внести счёт поставщика</Button>
      </div>}>
      {bills.items.length === 0 ? (
        <div className="muted">Счетов пока нет. Когда поставщик пришлёт счёт на детали или работы, внесите его сюда: сумма попадёт в расходы на ремонт.</div>
      ) : (
        <>
          <ul className="parts-list">
            {bills.items.map((b) => (
              <li key={b.id}>
                <div className="grow">
                  <Link to={`/bills/${b.id}`}><strong>Счёт № {b.number}</strong></Link> <span className="muted small">от {fmtDate(b.bill_date)}</span>
                  <div className="muted small">{b.supplier_name} · {b.kind_label}</div>
                </div>
                <div className="num"><strong>{fmtMoney(b.total)} ₽</strong></div>
                <Badge tone={b.status === "paid" ? "success" : b.status === "cancelled" ? "neutral" : "warning"} icon={ReceiptText}>{b.status_label}</Badge>
              </li>
            ))}
          </ul>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 10 }}>
            <span className="muted">Всего по обращению (без отменённых)</span><strong>{fmtMoney(bills.total)} ₽</strong>
          </div>
        </>
      )}
    </Card>
  );
}
