// Карточка счёта на ремонт: данные, строки, файлы, оплата, архив.
import { useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, ArrowLeft, Banknote, FileText, PackagePlus, Paperclip, Pencil, Printer, Trash2 } from "lucide-react";
import { api, get, post } from "../api";
import { fmtDate, fmtDateTime, fmtMoney, fmtSize, todayIso } from "../format";
import { Alert, Badge, Button, Card, ConfirmModal, Field, Modal, NotFound, PageHeader, QueryState, Spinner, Table, useForm, useToast } from "../ui";
import { useAuth } from "../auth";
import { ReceiveBillModal } from "../components/stock";
import { BillStatus } from "./Bills";

interface Item { pos: number; name: string; article: string; qty_text: string; unit: string; price: number; amount: number; vat: number }
interface FileRow { id: number; name: string; content_type: string; size: number; is_image: boolean; added_at: string; added_by: string | null }
interface Detail {
  id: number; legal_entity_id: number; legal_entity: string; kind: string; kind_label: string; number: string; bill_date: string;
  supplier_name: string; supplier_inn: string; supplier_kpp: string; supplier_addr: string; supplier_phone: string; bank_name: string; bik: string; corr_account: string; account: string;
  basis: string; vehicle_id: number | null; vehicle: string; request_id: number | null; request: { id: number | null; number: number; title: string; status_label: string } | null;
  vat_pct: number; vat_label: string; total: number; vat: number; words: string; status: string; status_label: string; paid_date: string | null; note: string;
  archived: boolean; archive_reason: string; created_by: string | null; created_at: string; updated_at: string; items: Item[]; files: FileRow[];
  can: { edit: boolean; pay: boolean; void: boolean; restore: boolean; print: boolean; files: boolean };
}

function PayModal({ b, onClose }: { b: Detail; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ status: b.status, paid_date: b.paid_date ?? todayIso() });
  const m = useMutation({
    mutationFn: () => post(`/bills/${b.id}/status`, { status: f.values.status, paid_date: f.values.status === "paid" ? f.values.paid_date || null : null }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Состояние счёта сохранено"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Оплата счёта" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button></>}>
      <div className="stack">
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <Field label="Состояние" error={f.errors.status} hint="«Отменён» — счёт не нужно платить: он не попадёт в расходы на ремонт">
          <select className="input" value={f.values.status} onChange={(e) => f.set("status", e.target.value)}>
            <option value="unpaid">Не оплачен</option><option value="paid">Оплачен</option><option value="cancelled">Отменён</option>
          </select>
        </Field>
        {f.values.status === "paid" && <Field label="Дата оплаты" error={f.errors.paid_date}><input className="input" type="date" max={todayIso()} min={b.bill_date} value={f.values.paid_date} onChange={(e) => f.set("paid_date", e.target.value)} /></Field>}
      </div>
    </Modal>
  );
}

export function BillCard() {
  const { id } = useParams();
  const bid = Number(id);
  const nav = useNavigate();
  const auto = (useLocation().state as { auto?: { how: string; note: string } } | null)?.auto;
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [modal, setModal] = useState<"pay" | "void" | "stock" | null>(null);
  const [rmFile, setRmFile] = useState<FileRow | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const q = useQuery({ queryKey: ["bill", bid], enabled: Number.isInteger(bid), queryFn: () => get<Detail>(`/bills/${bid}`) });
  const restore = useMutation({
    mutationFn: () => post(`/bills/${bid}/restore`),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Счёт возвращён из архива"); },
    onError: (e: any) => toast.error(e.message),
  });
  const add = useMutation({
    mutationFn: (files: File[]) => { const fd = new FormData(); files.forEach((x) => fd.append("files", x)); return api<{ added: number }>("POST", `/bills/${bid}/files`, fd); },
    onSuccess: (r) => { qc.invalidateQueries(); toast.success(r.added ? "Файл приложен" : "Такой файл уже приложен"); if (fileRef.current) fileRef.current.value = ""; },
    onError: (e: any) => toast.error(e.message),
  });
  const b = q.data;
  if (!Number.isInteger(bid)) return <NotFound what="Счёт" />;
  if (q.isLoading) return <Spinner />;
  if (q.error || !b) return <QueryState q={q} what="Счёт"><></></QueryState>;

  return (
    <>
      <PageHeader crumb={<Link to="/bills"><ArrowLeft size={13} aria-hidden /> Счета на ремонт</Link>}
        title={<span className="row wrap" style={{ gap: 12 }}>Счёт № {b.number}<BillStatus code={b.status} label={b.status_label} />{b.archived && <Badge>в архиве</Badge>}</span>}
        sub={`от ${fmtDate(b.bill_date)} · ${b.kind_label} · ${b.legal_entity}`}
        actions={<>
          {!b.archived && b.status !== "cancelled" && b.items.length > 0 && can("stock_moves.create", b.legal_entity_id) && <Button icon={PackagePlus} onClick={() => setModal("stock")}>Принять на склад</Button>}
          {b.can.print && <Button icon={Printer} onClick={() => nav(`/print/bills/${b.id}`)}>Печать</Button>}
          {b.can.pay && <Button variant={b.status === "unpaid" ? "primary" : "default"} icon={Banknote} onClick={() => setModal("pay")}>{b.status === "unpaid" ? "Отметить оплату" : "Оплата"}</Button>}
          {b.can.edit && <Button icon={Pencil} onClick={() => nav(`/bills/${b.id}/edit`)}>Изменить</Button>}
          {b.can.void && <Button variant="danger" icon={Archive} onClick={() => setModal("void")}>В архив</Button>}
          {b.can.restore && <Button icon={ArchiveRestore} loading={restore.isPending} onClick={() => restore.mutate()}>Вернуть из архива</Button>}
        </>} />
      {auto && <Alert tone={auto.how === "pdf" ? "info" : "warning"}>{auto.note || "Счёт прочитан из файла автоматически."} Если что-то не так, нажмите «Изменить».</Alert>}
      {b.archived && <Alert tone="warning">Счёт в архиве{b.archive_reason ? `: ${b.archive_reason}` : ""}. В итоги расходов на ремонт он не входит.</Alert>}
      {b.status === "cancelled" && !b.archived && <Alert tone="info">Счёт отменён: в итоги расходов на ремонт он не входит.</Alert>}
      <div className="grid cols-2" style={{ marginTop: 16, alignItems: "start" }}>
        <div className="stack">
          <Card title="Счёт">
            <dl className="kv">
              <dt>Сумма</dt><dd><strong style={{ fontSize: 18 }}>{fmtMoney(b.total)} ₽</strong> <span className="muted">в т.ч. НДС {b.vat_pct ? `${b.vat_pct} %` : "нет"}: {fmtMoney(b.vat)} ₽</span></dd>
              <dt>Прописью</dt><dd>{b.words}</dd>
              <dt>Оплата</dt><dd>{b.status === "paid" ? <>оплачен {fmtDate(b.paid_date)}</> : b.status === "cancelled" ? "отменён" : <span style={{ color: "var(--warning)" }}>ещё не оплачен</span>}</dd>
              <dt>Основание</dt><dd>{b.basis || <span className="muted">не указано</span>}</dd>
              <dt>Машина</dt><dd>{b.vehicle_id ? <Link to={`/vehicles/${b.vehicle_id}`}>{b.vehicle}</Link> : <span className="muted">не привязан</span>}</dd>
              <dt>Обращение</dt><dd>{b.request ? (b.request.id ? <Link to={`/requests/${b.request.id}`}>№ {b.request.number}</Link> : `№ ${b.request.number}`) : <span className="muted">не привязан</span>}{b.request?.title && <span className="muted"> · {b.request.title} ({b.request.status_label})</span>}</dd>
              {b.note && <><dt>Заметка</dt><dd style={{ whiteSpace: "pre-wrap" }}>{b.note}</dd></>}
              <dt>Внесён</dt><dd>{fmtDateTime(b.created_at)}{b.created_by ? `, ${b.created_by}` : ""}</dd>
            </dl>
          </Card>
          <Card title="Поставщик">
            <dl className="kv">
              <dt>Название</dt><dd><strong>{b.supplier_name}</strong></dd>
              <dt>ИНН / КПП</dt><dd>{b.supplier_inn || "—"}{b.supplier_kpp ? ` / ${b.supplier_kpp}` : ""}</dd>
              {b.supplier_addr && <><dt>Адрес</dt><dd>{b.supplier_addr}</dd></>}
              {b.supplier_phone && <><dt>Телефон</dt><dd>{b.supplier_phone}</dd></>}
              {(b.bank_name || b.bik || b.account) && <><dt>Банк</dt><dd>{b.bank_name || "—"}{b.bik ? `, БИК ${b.bik}` : ""}</dd><dt>Счета</dt><dd>р/с {b.account || "—"}{b.corr_account ? `, к/с ${b.corr_account}` : ""}</dd></>}
            </dl>
          </Card>
        </div>
        <div className="stack">
          {b.kind === "issued" && (
            <Card flush title="Что в счёте">
              <Table rows={b.items} rowKey={(r) => r.pos}
                columns={[
                  { key: "p", header: "№", width: 40, render: (r) => r.pos },
                  { key: "n", header: "Наименование", render: (r) => <>{r.name}{r.article && <span className="muted small"> · арт. {r.article}</span>}</> },
                  { key: "q", header: "Кол-во", num: true, render: (r) => `${r.qty_text} ${r.unit}` },
                  { key: "pr", header: "Цена, ₽", num: true, hideSm: true, render: (r) => fmtMoney(r.price) },
                  { key: "a", header: "Сумма, ₽", num: true, render: (r) => fmtMoney(r.amount) },
                ]} />
            </Card>
          )}
          {b.kind === "uploaded" && (
            <Card title="Файлы счёта" actions={b.can.files && <><Button size="sm" icon={Paperclip} loading={add.isPending} onClick={() => fileRef.current?.click()}>Приложить</Button>
              <input ref={fileRef} type="file" hidden multiple accept="application/pdf,image/jpeg,image/png,image/webp" onChange={(e) => e.target.files?.length && add.mutate([...e.target.files])} /></>}>
              {b.files.length === 0 ? <div className="muted">Файл не приложен. {b.can.files && "Приложите PDF или фото счёта: так его легко найти при сверке."}</div> : (
                <ul className="file-list big">
                  {b.files.map((x) => (
                    <li key={x.id}>
                      <a href={`/api/bills/files/${x.id}`} target="_blank" rel="noreferrer" className="file-thumb" aria-label={`Открыть ${x.name}`}>
                        {x.is_image ? <img src={`/api/bills/files/${x.id}?thumb=true`} alt="" loading="lazy" /> : <FileText size={28} aria-hidden />}
                      </a>
                      <div className="grow"><a href={`/api/bills/files/${x.id}`} target="_blank" rel="noreferrer"><strong>{x.name}</strong></a><div className="muted small">{fmtSize(x.size)} · {fmtDateTime(x.added_at)}{x.added_by ? `, ${x.added_by}` : ""}</div></div>
                      {b.can.edit && <Button variant="ghost" size="sm" icon={Trash2} aria-label={`Убрать файл ${x.name}`} onClick={() => setRmFile(x)} />}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}
        </div>
      </div>
      {modal === "pay" && <PayModal b={b} onClose={() => setModal(null)} />}
      {modal === "stock" && <ReceiveBillModal billId={b.id} onClose={() => setModal(null)} />}
      {modal === "void" && (
        <ConfirmModal title="Убрать счёт в архив?" danger confirmLabel="В архив" reasonLabel="Причина" text="Счёт не стирается: его можно вернуть. В архиве он не входит в итоги расходов на ремонт."
          onConfirm={async (reason) => { await post(`/bills/${b.id}/void`, { reason }); qc.invalidateQueries(); toast.success("Счёт убран в архив"); }} onClose={() => setModal(null)} />
      )}
      {rmFile && (
        <ConfirmModal title="Убрать файл?" danger confirmLabel="Убрать" reasonLabel="Причина" reasonRequired={false} text={<>Файл «{rmFile.name}» перестанет показываться в счёте. В журнале действий останется запись.</>}
          onConfirm={async (reason) => { await post(`/bills/files/${rmFile.id}/remove`, { reason }); qc.invalidateQueries(); toast.success("Файл убран"); }} onClose={() => setRmFile(null)} />
      )}
    </>
  );
}
