// Техобслуживание: кнопка «Выполнено» в строке. Закрывает ТО сегодняшним днём и, если задана периодичность, создаёт следующее.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckCheck } from "lucide-react";
import { post } from "../api";
import { useAuth } from "../auth";
import { fmtNum } from "../format";
import { Alert, Button, Field, Modal, useForm, useToast } from "../ui";
import type { Row, RowActions } from "./Registry";

const KINDS: Record<string, string> = { so: "СО", to1: "ТО-1", to2: "ТО-2", other: "Другое" };

function DoneModal({ row, onClose, onDone }: { row: Row; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const f = useForm({ note: "" });
  const m = useMutation({
    mutationFn: () => post<{ next: Row | null }>(`/maintenance/${row.id}/done`, { note: f.values.note }),
    onSuccess: (r) => {
      toast.success(r.next ? `ТО выполнено. Следующее создано: через ${fmtNum(row.interval_km)} км от текущего пробега` : "ТО отмечено выполненным");
      onDone(); onClose();
    },
    onError: f.fail,
  });
  return (
    <Modal title="ТО выполнено" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Выполнено</Button></>}>
      <div className="stack-sm">
        <p><strong>{row.vehicle_id_label}</strong> · {KINDS[row.kind] ?? row.kind}{row.work ? `: ${row.work}` : ""}</p>
        <p className="muted small">
          Запись закроется сегодняшним числом и уйдёт с контроля.
          {row.interval_km ? ` Следующее ТО создадим автоматически: пробег машины плюс ${fmtNum(row.interval_km)} км.` : " Периодичность не задана, поэтому следующее ТО нужно добавить самим."}
        </p>
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <Field label="Заметка" error={f.errors.note}><input className="input" maxLength={300} value={f.values.note} onChange={(e) => f.set("note", e.target.value)} placeholder="Например: сервис на Ленина, заменили масло" /></Field>
      </div>
    </Modal>
  );
}

function DoneButton({ row, done }: { row: Row; done: () => void }) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  if (!can("maintenance.edit", row.legal_entity_id)) return null;
  return (
    <>
      <Button variant="ghost" size="sm" icon={CheckCheck} aria-label="Выполнено" title="ТО выполнено" onClick={() => setOpen(true)} />
      {open && <DoneModal row={row} onClose={() => setOpen(false)} onDone={done} />}
    </>
  );
}

export const maintenanceActions: RowActions = (r, done) => (!r.archived && !r.done_at ? { width: 1, node: <DoneButton row={r} done={done} /> } : null);
