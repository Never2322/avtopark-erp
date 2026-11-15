import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Search, Wrench } from "lucide-react";
import { get, post } from "../api";
import { useAuth } from "../auth";
import { fmtDate } from "../format";
import { Page, useDebounced, useEntityParam, useUrlState } from "../hooks";
import { VehicleSelect } from "../components/pickers";
import { emptyPart, filledParts, PartsEditor, type PartRow } from "../components/PartsEditor";
import { Alert, Badge, Button, Card, Empty, Field, FilterChips, Modal, PageHeader, Pagination, Priority, QueryState, RequestStatus, Table, useForm, useToast } from "../ui";

export interface RequestRow {
  id: number; number: number; legal_entity_id: number; title: string; priority: string; priority_label: string; status: string; status_label: string;
  open: boolean; vehicle: { id: number; plate: string; name: string; status: string }; reporter: string | null; assignee: string | null;
  due_date: string | null; created_at: string; overdue: boolean;
}
interface RequestPage extends Page<RequestRow> { counts: Record<string, number>; open_total: number }

const PRIORITIES = [["low", "Низкий"], ["normal", "Обычный"], ["high", "Высокий"], ["urgent", "Срочный"]];

function CreateRequest({ vehicleId, onClose }: { vehicleId?: string; onClose: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const entity = useEntityParam();
  const f = useForm({ vehicle_id: vehicleId ?? "", title: "", description: "", priority: "normal" });
  const [parts, setParts] = useState<PartRow[]>([emptyPart()]);
  const m = useMutation({
    mutationFn: () => post<{ id: number }>("/requests", { ...f.values, vehicle_id: Number(f.values.vehicle_id), parts: filledParts(parts).map(({ name, article, qty, unit }) => ({ name, article, qty, unit })) }),
    onSuccess: (r) => { qc.invalidateQueries(); toast.success("Обращение создано"); onClose(); nav(`/requests/${r.id}`); },
    onError: f.fail,
  });
  return (
    <Modal title="Новое обращение" onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" loading={m.isPending} disabled={!f.values.vehicle_id || f.values.title.trim().length < 3} onClick={() => { f.clear(); m.mutate(); }}>Создать</Button>
    </>}>
      <div className="stack">
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <VehicleSelect value={f.values.vehicle_id} entityId={entity} error={f.errors.vehicle_id} onChange={(v) => f.set("vehicle_id", v)} />
        <Field label="Что случилось (коротко)" error={f.errors.title}><input className="input" value={f.values.title} maxLength={200} onChange={(e) => f.set("title", e.target.value)} placeholder="Например: стук в передней подвеске" /></Field>
        <Field label="Подробности" error={f.errors.description}><textarea className="input" value={f.values.description} maxLength={4000} onChange={(e) => f.set("description", e.target.value)} /></Field>
        <Field label="Насколько срочно" error={f.errors.priority}>
          <select className="input" value={f.values.priority} onChange={(e) => f.set("priority", e.target.value)}>{PRIORITIES.map(([c, l]) => <option key={c} value={c}>{l}</option>)}</select>
        </Field>
        <div className="field full">
          <span className="label">Что нужно купить (по желанию)</span>
          <PartsEditor rows={parts} onChange={setParts} errors={f.errors.parts ?? f.errors.items ?? f.errors.name ?? f.errors.qty ?? f.errors.unit ?? f.errors.article} hint="Список можно дополнить позже в карточке обращения. Механик отметит, что куплено." />
        </div>
      </div>
    </Modal>
  );
}

export function RequestsTable({ vehicleId }: { vehicleId: number }) {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["requests", "vehicle", vehicleId], queryFn: () => get<RequestPage>("/requests", { vehicle_id: vehicleId, page_size: 100 }) });
  return (
    <QueryState q={q}>
      {q.data && (q.data.items.length === 0 ? <Empty icon={Wrench} title="Обращений по этой машине нет" /> : (
        <Table rows={q.data.items} rowKey={(r) => r.id} onRowClick={(r) => nav(`/requests/${r.id}`)}
          columns={[
            { key: "n", header: "№", render: (r) => <strong>№{r.number}</strong> },
            { key: "t", header: "Суть", render: (r) => r.title },
            { key: "s", header: "Статус", render: (r) => <RequestStatus code={r.status} label={r.status_label} /> },
            { key: "d", header: "Создано", hideSm: true, render: (r) => fmtDate(r.created_at) },
          ]} />
      ))}
    </QueryState>
  );
}

export function Requests() {
  const { can } = useAuth();
  const entity = useEntityParam();
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const [s, setS] = useUrlState({ q: "", status: "open", priority: "", assignee: "", page: "1" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  const [creating, setCreating] = useState<null | { vehicle?: string }>(sp.get("new") === "1" ? { vehicle: sp.get("vehicle") ?? undefined } : null);

  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);
  useEffect(() => {
    if (sp.get("new") === "1") {
      setCreating({ vehicle: sp.get("vehicle") ?? undefined });
      setSp((o) => { o.delete("new"); o.delete("vehicle"); return o; }, { replace: true });
    }
  }, [sp, setSp]);

  const params = { q: s.q, status: s.status === "all" ? "" : s.status, priority: s.priority, assignee: s.assignee, legal_entity_id: entity, page: Number(s.page), page_size: 25 };
  const list = useQuery({ queryKey: ["requests", params], queryFn: () => get<RequestPage>("/requests", params), placeholderData: (p) => p });
  const c = list.data?.counts;
  const total = c ? Object.values(c).reduce((a, b) => a + b, 0) : undefined;

  return (
    <>
      <PageHeader title="Обращения и ремонты" sub={list.data ? `Открытых: ${list.data.open_total}` : undefined}
        actions={can("requests.create") && <Button variant="primary" icon={Plus} onClick={() => setCreating({})}>Новое обращение</Button>} />
      <Card flush>
        <div className="toolbar" style={{ padding: "14px 14px 0" }}>
          <div style={{ position: "relative", minWidth: 240, flex: "1 1 240px", maxWidth: 360 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Номер, суть, госномер" aria-label="Поиск по обращениям" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <select className="input compact" aria-label="Приоритет" value={s.priority} onChange={(e) => setS({ priority: e.target.value })}>
            <option value="">Любой приоритет</option>
            {PRIORITIES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
          </select>
          {can("requests.edit") && (
            <select className="input compact" aria-label="Исполнитель" value={s.assignee} onChange={(e) => setS({ assignee: e.target.value })}>
              <option value="">Любой исполнитель</option><option value="me">Мои</option><option value="none">Без исполнителя</option>
            </select>
          )}
        </div>
        <div style={{ padding: "12px 14px" }}>
          <FilterChips value={s.status} onChange={(v) => setS({ status: v })}
            options={[
              { id: "open", label: "Открытые", count: list.data?.open_total },
              { id: "all", label: "Все", count: total },
              { id: "new", label: "Новые", count: c?.new }, { id: "diagnostics", label: "Диагностика", count: c?.diagnostics },
              { id: "approval", label: "Согласование", count: c?.approval }, { id: "in_progress", label: "В работе", count: c?.in_progress },
              { id: "waiting_parts", label: "Ждут запчасти", count: c?.waiting_parts }, { id: "done", label: "Выполнены", count: c?.done },
              { id: "closed", label: "Закрыты", count: c?.closed }, { id: "cancelled", label: "Отменены", count: c?.cancelled },
            ]} />
        </div>
        <QueryState q={list}>
          {list.data && (list.data.items.length === 0 ? (
            <Empty icon={Wrench} title={s.q || s.priority || s.assignee || s.status !== "open" ? "Ничего не найдено" : "Открытых обращений нет"}>
              {(s.q || s.priority || s.assignee || s.status !== "open") ? <Button onClick={() => { setText(""); setS({ q: "", priority: "", assignee: "", status: "open" }); }}>Сбросить фильтры</Button> : "Когда что-то сломается, обращение появится здесь."}
            </Empty>
          ) : (
            <>
              <Table rows={list.data.items} rowKey={(r) => r.id} onRowClick={(r) => nav(`/requests/${r.id}`)} dim={(r) => !r.open}
                columns={[
                  { key: "n", header: "№", width: 70, render: (r) => <strong>№{r.number}</strong> },
                  { key: "v", header: "Машина", render: (r) => <><strong>{r.vehicle.plate}</strong><span className="muted"> {r.vehicle.name}</span></> },
                  { key: "t", header: "Суть", render: (r) => r.title },
                  { key: "p", header: "Срочность", hideSm: true, render: (r) => <Priority code={r.priority} label={r.priority_label} /> },
                  { key: "s", header: "Статус", render: (r) => <RequestStatus code={r.status} label={r.status_label} /> },
                  { key: "a", header: "Исполнитель", hideSm: true, render: (r) => r.assignee ?? <span className="muted">не назначен</span> },
                  { key: "d", header: "Срок", hideSm: true, render: (r) => r.due_date ? (r.overdue ? <Badge tone="error">{fmtDate(r.due_date)} · просрочено</Badge> : fmtDate(r.due_date)) : <span className="muted">—</span> },
                ]} />
              <Pagination page={list.data.page} pageSize={list.data.page_size} total={list.data.total} onPage={(p) => setS({ page: String(p) })} />
            </>
          ))}
        </QueryState>
      </Card>
      {creating && <CreateRequest vehicleId={creating.vehicle} onClose={() => setCreating(null)} />}
    </>
  );
}
