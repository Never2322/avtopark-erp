// Блок «События» в карточке сотрудника: медосмотры, инструктажи, нарушения, ДТП, премии и штрафы со сроками.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardList, Plus } from "lucide-react";
import { get } from "../api";
import { useAuth } from "../auth";
import { fmtDate, fmtMoney } from "../format";
import { RecordModal, type Meta, type Row } from "../pages/Registry";
import { Badge, Button, QueryState, Table, type Tone } from "../ui";

interface Ev extends Row {
  kind: string; event_date: string | null; due_date: string | null; amount: number | string; description: string; controlled: boolean;
  state: { text: string; tone: Tone }; left: string; legal_entity_id: number;
}

export function EmployeeEvents({ employeeId, entityIds }: { employeeId: number; entityIds: number[] }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [form, setForm] = useState<Ev | "new" | null>(null);
  const q = useQuery({ queryKey: ["reg", "employee_events", { employee_id: employeeId }], queryFn: () => get<{ items: Ev[] }>("/employee_events", { employee_id: employeeId, page_size: 100 }) });
  const meta = useQuery({ queryKey: ["reg-meta", "employee_events"], queryFn: () => get<Meta>("/employee_events/meta"), staleTime: 60_000 });
  const kinds = Object.fromEntries((meta.data?.fields.find((f) => f.name === "kind")?.options ?? []).map((o) => [o.value, o.label]));
  const items = q.data?.items ?? [];
  const canAdd = entityIds.some((id) => can("employee_events.create", id));
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["reg", "employee_events"] });
    qc.invalidateQueries({ queryKey: ["reminders"] });
    qc.invalidateQueries({ queryKey: ["calendar"] });
  };
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", margin: "6px 0 0" }}>
        <h3 style={{ fontSize: 14, display: "flex", alignItems: "center", gap: 6 }}><ClipboardList size={16} aria-hidden /> События</h3>
        <span className="row">
          {canAdd && <Button size="sm" icon={Plus} onClick={() => setForm("new")}>Добавить</Button>}
          <Link className="small" to="/employee-events">Все события</Link>
        </span>
      </div>
      <QueryState q={q}>
        {items.length === 0 ? <p className="muted small">Событий пока нет: медосмотр, инструктаж, нарушение, премия или штраф.</p> : (
          <Table rows={items} rowKey={(r) => r.id} dim={(r) => r.archived}
            onRowClick={(r) => setForm(r)}
            columns={[
              { key: "k", header: "Что", render: (r) => <><strong>{kinds[r.kind] ?? r.kind}</strong>{r.description ? <span className="muted"> · {r.description}</span> : null}{Number(r.amount) > 0 ? <span className="muted"> · {fmtMoney(Number(r.amount))}</span> : null}</> },
              { key: "e", header: "Дата", hideSm: true, render: (r) => (r.event_date ? fmtDate(r.event_date) : "—") },
              { key: "d", header: "Следующий срок", render: (r) => (r.due_date ? fmtDate(r.due_date) : "—") },
              { key: "s", header: "Срок", render: (r) => (r.archived ? <Badge>В архиве</Badge> : <Badge tone={r.state.tone}>{r.state.text}</Badge>) },
            ]} />
        )}
      </QueryState>
      {form && meta.data && (
        <RecordModal meta={meta.data} row={form === "new" ? null : form} defaults={{ employee_id: String(employeeId) }} onClose={() => setForm(null)} onSaved={refresh} />
      )}
    </>
  );
}
