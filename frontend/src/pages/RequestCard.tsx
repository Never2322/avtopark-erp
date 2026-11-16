import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, MessageSquare, Pencil, Send, UserCheck } from "lucide-react";
import { get, patch, post } from "../api";
import { fmtDate, fmtDateTime, fmtNum } from "../format";
import { useVehicleTypes } from "../hooks";
import { RequestBills, RequestParts, type BillsBlock, type PartsBlock } from "../components/RequestParts";
import { Alert, Badge, Button, Card, Field, Modal, numOrNull, PageHeader, Priority, NotFound, QueryState, RequestStatus, Spinner, useForm, useToast, VehicleStatus } from "../ui";

interface Ev { id: number; kind: string; text: string; created_at: string; author: string | null; from_label: string | null; to_label: string | null; to_status: string | null; from_status: string | null }
interface Detail {
  id: number; number: number; legal_entity_id: number; title: string; description: string; planned_work: string; priority: string; priority_label: string;
  status: string; status_label: string; open: boolean; vehicle: { id: number; plate: string; name: string; status: string };
  reporter: string | null; assignee: string | null; assignee_id: number | null; due_date: string | null; created_at: string; closed_at: string | null; overdue: boolean;
  currency: string; cost_estimate: number | null; cost_actual: number | null; money_visible: boolean;
  allowed_transitions: { status: string; label: string }[]; actions: { edit: boolean; comment: boolean; set_vehicle_status: boolean }; events: Ev[];
  parts: PartsBlock; bills: BillsBlock;
}

const TRANSITION_HINT: Record<string, string> = {
  diagnostics: "Взять в диагностику", approval: "Отправить на согласование", in_progress: "Начать ремонт", waiting_parts: "Ждём запчасти или подрядчика",
  done: "Работы выполнены", closed: "Закрыть обращение", cancelled: "Отменить",
};
/** Подпись кнопки зависит от того, откуда и куда переводим: одно и то же «в работу» значит разное. */
function transitionLabel(from: string, to: string): string {
  if (from === "approval" && to === "in_progress") return "Согласовать ремонт";
  if (from === "approval" && to === "cancelled") return "Отклонить ремонт";
  if (from === "approval" && to === "diagnostics") return "Вернуть на диагностику";
  if (from === "waiting_parts" && to === "in_progress") return "Запчасти есть, продолжаем";
  if (from === "done" && to === "in_progress") return "Вернуть в работу";
  return TRANSITION_HINT[to] ?? to;
}
// главная кнопка — шаг вперёд по обычному пути; «ждём запчасти» и «отмена» идут после
const TRANSITION_ORDER = ["diagnostics", "approval", "in_progress", "done", "closed", "waiting_parts", "cancelled"];

function TransitionModal({ r, to, onClose }: { r: Detail; to: { status: string; label: string }; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const types = useVehicleTypes();
  const f = useForm({ comment: "", vehicle_status: "" });
  const m = useMutation({
    mutationFn: () => post(`/requests/${r.id}/transition`, { to_status: to.status, comment: f.values.comment, vehicle_status: f.values.vehicle_status || null }),
    onSuccess: () => { qc.invalidateQueries(); toast.success(`Статус: ${to.label}`); onClose(); },
    onError: f.fail,
  });
  const cancel = to.status === "cancelled";
  const suggested = to.status === "in_progress" && r.status !== "waiting_parts" ? "in_repair" : to.status === "done" ? "in_work" : "";
  return (
    <Modal title={transitionLabel(r.status, to.status)} onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant={cancel ? "danger" : "primary"} loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>{cancel ? (r.status === "approval" ? "Отклонить" : "Отменить обращение") : "Перевести"}</Button>
    </>}>
      <div className="stack">
        <div className="row wrap"><RequestStatus code={r.status} label={r.status_label} /><ArrowRight size={16} aria-hidden /><strong>{to.label}</strong></div>
        {f.formError && !f.errors.comment && !f.errors.vehicle_status && <Alert tone="error">{f.formError}</Alert>}
        <Field label={cancel ? "Причина отмены (обязательно)" : "Комментарий (по желанию)"} error={f.errors.comment}>
          <textarea className="input" value={f.values.comment} maxLength={2000} onChange={(e) => f.set("comment", e.target.value)} />
        </Field>
        {r.actions.set_vehicle_status && !cancel && (
          <Field label="Заодно сменить статус машины" error={f.errors.vehicle_status} hint={suggested ? `Обычно здесь выбирают «${types.data?.statuses.find((s) => s.code === suggested)?.label}»` : undefined}>
            <select className="input" value={f.values.vehicle_status} onChange={(e) => f.set("vehicle_status", e.target.value)}>
              <option value="">Не менять (сейчас: {types.data?.statuses.find((s) => s.code === r.vehicle.status)?.label ?? r.vehicle.status})</option>
              {types.data?.statuses.filter((s) => s.code !== r.vehicle.status).map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
            </select>
          </Field>
        )}
      </div>
    </Modal>
  );
}

function EditModal({ r, onClose }: { r: Detail; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const assignees = useQuery({ queryKey: ["assignees", r.legal_entity_id], queryFn: () => get<{ items: { id: number; name: string }[] }>("/requests/assignees", { legal_entity_id: r.legal_entity_id }) });
  const f = useForm({
    title: r.title, description: r.description, priority: r.priority, planned_work: r.planned_work, due_date: r.due_date ?? "",
    assignee_id: r.assignee_id ? String(r.assignee_id) : "", cost_estimate: r.cost_estimate !== null ? String(r.cost_estimate) : "", cost_actual: r.cost_actual !== null ? String(r.cost_actual) : "",
  });
  const m = useMutation({
    mutationFn: () => {
      const v = f.values;
      const body: Record<string, unknown> = { title: v.title, description: v.description, priority: v.priority, planned_work: v.planned_work };
      if (v.due_date) body.due_date = v.due_date; else if (r.due_date) body.clear_due_date = true;
      if (v.assignee_id) body.assignee_id = Number(v.assignee_id); else if (r.assignee_id) body.clear_assignee = true;
      if (r.money_visible) { const e = numOrNull(v.cost_estimate), a = numOrNull(v.cost_actual); if (e !== null) body.cost_estimate = e; if (a !== null) body.cost_actual = a; }
      return patch(`/requests/${r.id}`, body);
    },
    onSuccess: () => { qc.invalidateQueries(); toast.success("Сохранено"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Изменить обращение" wide onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button></>}>
      <div className="stack">
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          <Field label="Суть" full error={f.errors.title}><input className="input" value={f.values.title} maxLength={200} onChange={(e) => f.set("title", e.target.value)} /></Field>
          <Field label="Подробности" full error={f.errors.description}><textarea className="input" value={f.values.description} maxLength={4000} onChange={(e) => f.set("description", e.target.value)} /></Field>
          <Field label="Срочность" error={f.errors.priority}>
            <select className="input" value={f.values.priority} onChange={(e) => f.set("priority", e.target.value)}>
              <option value="low">Низкий</option><option value="normal">Обычный</option><option value="high">Высокий</option><option value="urgent">Срочный</option>
            </select>
          </Field>
          <Field label="Срок" error={f.errors.due_date}><input className="input" type="date" value={f.values.due_date} onChange={(e) => f.set("due_date", e.target.value)} /></Field>
          <Field label="Исполнитель" error={f.errors.assignee_id} hint="Только сотрудники с правом вести обращения">
            <select className="input" value={f.values.assignee_id} onChange={(e) => f.set("assignee_id", e.target.value)}>
              <option value="">Не назначен</option>
              {assignees.data?.items.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          </Field>
          {r.money_visible && <>
            <Field label={`Смета, ${r.currency}`} error={f.errors.cost_estimate}><input className="input" inputMode="decimal" value={f.values.cost_estimate} onChange={(e) => f.set("cost_estimate", e.target.value)} /></Field>
            <Field label={`Фактически потрачено, ${r.currency}`} error={f.errors.cost_actual}><input className="input" inputMode="decimal" value={f.values.cost_actual} onChange={(e) => f.set("cost_actual", e.target.value)} /></Field>
          </>}
          <Field label="План работ" full error={f.errors.planned_work}><textarea className="input" value={f.values.planned_work} maxLength={4000} onChange={(e) => f.set("planned_work", e.target.value)} /></Field>
        </div>
      </div>
    </Modal>
  );
}

export function RequestCard() {
  const { id } = useParams();
  const rid = Number(id);
  const qc = useQueryClient();
  const toast = useToast();
  const [modal, setModal] = useState<{ kind: "edit" } | { kind: "to"; to: { status: string; label: string } } | null>(null);
  const [text, setText] = useState("");
  const q = useQuery({ queryKey: ["request", rid], enabled: Number.isInteger(rid), queryFn: () => get<Detail>(`/requests/${rid}`) });
  const send = useMutation({
    mutationFn: () => post(`/requests/${rid}/comments`, { text }),
    onSuccess: () => { setText(""); qc.invalidateQueries(); },
    onError: (e: any) => toast.error(e.message),
  });
  const r = q.data;
  if (q.isLoading) return <Spinner />;
  if (!Number.isInteger(rid)) return <NotFound what="Обращение" />;
  if (q.error || !r) return <QueryState q={q} what="Обращение"><></></QueryState>;
  const money = (v: number | null) => (v === null ? "—" : `${fmtNum(v, 2)} ${r.currency}`);

  return (
    <>
      <PageHeader crumb={<Link to="/requests"><ArrowLeft size={13} aria-hidden /> Обращения</Link>}
        title={<span className="row wrap" style={{ gap: 12 }}>Обращение №{r.number}<RequestStatus code={r.status} label={r.status_label} /><Priority code={r.priority} label={r.priority_label} /></span>}
        sub={r.title}
        actions={<>
          {[...r.allowed_transitions].sort((a, b) => TRANSITION_ORDER.indexOf(a.status) - TRANSITION_ORDER.indexOf(b.status)).map((t, i) => (
            <Button key={t.status} variant={t.status === "cancelled" ? "danger" : i === 0 ? "primary" : "default"} onClick={() => setModal({ kind: "to", to: t })}>
              {transitionLabel(r.status, t.status)}
            </Button>
          ))}
          {r.actions.edit && <Button icon={Pencil} onClick={() => setModal({ kind: "edit" })}>Изменить</Button>}
        </>} />
      {r.status === "approval" && !r.allowed_transitions.length && <Alert tone="info">Ремонт ждёт согласования. Решение принимает тот, у кого есть право согласования.</Alert>}
      {!r.open && <Alert tone="info">Обращение закрыто: изменить его или добавить комментарий уже нельзя. Вся история ниже сохранена.</Alert>}
      <div className="grid cols-2" style={{ marginTop: 16, alignItems: "start" }}>
        <div className="stack">
          <Card title="О чём обращение">
            <dl className="kv">
              <dt>Машина</dt><dd><Link to={`/vehicles/${r.vehicle.id}`}><strong>{r.vehicle.plate}</strong></Link> <span className="muted">{r.vehicle.name}</span> <VehicleStatus code={r.vehicle.status} label={({ in_work: "На линии", in_repair: "В ремонте" } as Record<string, string>)[r.vehicle.status] ?? r.vehicle.status} /></dd>
              <dt>Описание</dt><dd>{r.description || <span className="muted">не заполнено</span>}</dd>
              <dt>План работ</dt><dd>{r.planned_work || <span className="muted">не заполнен</span>}</dd>
              <dt>Кто сообщил</dt><dd>{r.reporter ?? "—"}</dd>
              <dt>Исполнитель</dt><dd>{r.assignee ?? <span className="muted">не назначен</span>}</dd>
              <dt>Срок</dt><dd>{r.due_date ? <>{fmtDate(r.due_date)} {r.overdue && <Badge tone="error">просрочено</Badge>}</> : <span className="muted">не задан</span>}</dd>
              <dt>Создано</dt><dd>{fmtDateTime(r.created_at)}</dd>
              {r.closed_at && <><dt>Закрыто</dt><dd>{fmtDateTime(r.closed_at)}</dd></>}
              {r.money_visible && <><dt>Смета</dt><dd>{money(r.cost_estimate)}</dd><dt>Потрачено</dt><dd>{money(r.cost_actual)}</dd></>}
            </dl>
            {!r.money_visible && <div className="muted small" style={{ marginTop: 10 }}>Суммы ремонта видят только те, кто ведёт обращения и согласует ремонт.</div>}
          </Card>
          <RequestParts requestId={r.id} parts={r.parts} open={["new", "diagnostics", "approval", "in_progress", "waiting_parts"].includes(r.status)} />
          <RequestBills requestId={r.id} bills={r.bills} />
        </div>
        <Card title="История и комментарии">
          {r.actions.comment && (
            <form className="row" style={{ marginBottom: 14, alignItems: "flex-start" }} onSubmit={(e) => { e.preventDefault(); if (text.trim()) send.mutate(); }}>
              <textarea className="input" style={{ minHeight: 44 }} aria-label="Комментарий" placeholder="Написать комментарий" value={text} maxLength={4000} onChange={(e) => setText(e.target.value)} />
              <Button type="submit" variant="primary" icon={Send} loading={send.isPending} disabled={!text.trim()} aria-label="Отправить комментарий" />
            </form>
          )}
          <ul className="timeline">
            {[...r.events].reverse().map((e) => (
              <li key={e.id}>
                <span className={`dot ${e.kind === "status" ? "mint" : ""}`}>{e.kind === "comment" ? <MessageSquare size={12} aria-hidden /> : e.kind === "assign" ? <UserCheck size={12} aria-hidden /> : e.kind === "edit" ? <Pencil size={12} aria-hidden /> : <ArrowRight size={12} aria-hidden />}</span>
                <div>
                  {e.kind === "status" && <div className="row wrap">{e.from_label ? <><span className="muted">{e.from_label}</span><span className="muted">→</span></> : null}<strong>{e.to_label}</strong></div>}
                  {e.text && <div style={{ marginTop: e.kind === "status" ? 4 : 0, whiteSpace: "pre-wrap" }}>{e.text}</div>}
                  <div className="muted small">{fmtDateTime(e.created_at)} · {e.author ?? "система"}</div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      {modal?.kind === "to" && <TransitionModal r={r} to={modal.to} onClose={() => setModal(null)} />}
      {modal?.kind === "edit" && <EditModal r={r} onClose={() => setModal(null)} />}
    </>
  );
}
