// Вкладка «СИЗ» в карточке сотрудника: что выдано, до какого срока, что пора менять.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { HardHat } from "lucide-react";
import { get } from "../api";
import { useAuth } from "../auth";
import { fmtDate } from "../format";
import { ReplaceModal } from "../pages/ppeActions";
import { Badge, Button, QueryState, Table, type Tone } from "../ui";

interface Issue {
  id: number; ppe_id_label: string; employee_id_label: string; issue_date: string; due_date: string | null; returned_date: string | null; qty: number; size: string;
  state: { text: string; tone: Tone }; left: string; can_edit: boolean; legal_entity_id: number; archived: boolean;
}

export function EmployeePpe({ employeeId }: { employeeId: number }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [replace, setReplace] = useState<Issue | null>(null);
  const q = useQuery({ queryKey: ["reg", "ppe", { employee_id: employeeId }], queryFn: () => get<{ items: Issue[] }>("/ppe", { employee_id: employeeId, page_size: 100 }) });
  const items = q.data?.items ?? [];
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", margin: "6px 0 0" }}>
        <h3 style={{ fontSize: 14, display: "flex", alignItems: "center", gap: 6 }}><HardHat size={16} aria-hidden /> СИЗ</h3>
        <Link className="small" to="/ppe">Открыть раздел «Выдача СИЗ»</Link>
      </div>
      <QueryState q={q}>
        {items.length === 0 ? <p className="muted small">Ничего не выдавали.</p> : (
          <Table rows={items} rowKey={(r) => r.id} dim={(r) => !!r.returned_date}
            columns={[
              { key: "n", header: "Что", render: (r) => <><strong>{r.ppe_id_label}</strong>{r.size ? <span className="muted"> · р. {r.size}</span> : null}{r.qty > 1 ? <span className="muted"> · {r.qty} шт.</span> : null}</> },
              { key: "i", header: "Выдано", hideSm: true, render: (r) => fmtDate(r.issue_date) },
              { key: "d", header: "Заменить до", render: (r) => (r.due_date ? fmtDate(r.due_date) : "—") },
              { key: "s", header: "Состояние", render: (r) => <Badge tone={r.state.tone}>{r.state.text}</Badge> },
              { key: "x", header: "", render: (r) => !r.returned_date && !r.archived && can("ppe.edit", r.legal_entity_id) && can("ppe.create", r.legal_entity_id)
                ? <Button size="sm" onClick={() => setReplace(r)}>Заменить</Button> : null },
            ]} />
        )}
      </QueryState>
      {replace && <ReplaceModal row={replace as any} onClose={() => setReplace(null)} onDone={() => qc.invalidateQueries({ queryKey: ["reg", "ppe"] })} />}
    </>
  );
}
