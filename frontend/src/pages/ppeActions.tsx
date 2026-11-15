// СИЗ: кнопка «Заменить» в строке выдачи. Старая вещь считается возвращённой в день замены, новая выдаётся тому же человеку.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Repeat } from "lucide-react";
import { post } from "../api";
import { useAuth } from "../auth";
import { todayIso } from "../format";
import { Alert, Button, Field, Modal, useForm, useToast } from "../ui";
import type { Row, RowActions } from "./Registry";

export function ReplaceModal({ row, onClose, onDone }: { row: Row; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const f = useForm({ issue_date: todayIso(), qty: String(row.qty ?? 1), size: String(row.size ?? ""), note: "" });
  const m = useMutation({
    mutationFn: () => post(`/ppe/${row.id}/replace`, { issue_date: f.values.issue_date || null, qty: Number(f.values.qty) || null, size: f.values.size, note: f.values.note }),
    onSuccess: () => { toast.success("Заменено: старая вещь возвращена, выдана новая"); onDone(); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Заменить СИЗ" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Заменить</Button></>}>
      <div className="stack-sm">
        <p><strong>{row.ppe_id_label}</strong> · {row.employee_id_label}</p>
        <p className="muted small">Выдана {row.issue_date ? new Date(row.issue_date).toLocaleDateString("ru-RU") : "—"}. Её отметим возвращённой в день замены, а новую выдадим тому же сотруднику; срок замены посчитается по нормам.</p>
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          <Field label="Дата замены" error={f.errors.issue_date}><input className="input" type="date" value={f.values.issue_date} onChange={(e) => f.set("issue_date", e.target.value)} /></Field>
          <Field label="Количество" error={f.errors.qty}><input className="input" inputMode="numeric" value={f.values.qty} onChange={(e) => f.set("qty", e.target.value)} /></Field>
          <Field label="Размер" error={f.errors.size}><input className="input" maxLength={20} value={f.values.size} onChange={(e) => f.set("size", e.target.value)} /></Field>
          <Field label="Заметка" error={f.errors.note} full><input className="input" maxLength={300} value={f.values.note} onChange={(e) => f.set("note", e.target.value)} placeholder="Например: износилась" /></Field>
        </div>
      </div>
    </Modal>
  );
}

function ReplaceButton({ row, done }: { row: Row; done: () => void }) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  if (!can("ppe.edit", row.legal_entity_id) || !can("ppe.create", row.legal_entity_id)) return null;
  return (
    <>
      <Button variant="ghost" size="sm" icon={Repeat} aria-label="Заменить" title="Заменить: вернуть старую и выдать новую" onClick={() => setOpen(true)} />
      {open && <ReplaceModal row={row} onClose={() => setOpen(false)} onDone={done} />}
    </>
  );
}

export const ppeActions: RowActions = (r, done) => (!r.archived && !r.returned_date ? { width: 1, node: <ReplaceButton row={r} done={done} /> } : null);
