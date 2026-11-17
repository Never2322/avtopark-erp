// Кнопка водителя «Машина в ремонте» / «Машина на линии». Меняет статус своей машины сам, без звонка диспетчеру.
// Для ремонта нужна причина (что случилось): она попадает в историю статусов и в сообщение механику, диспетчеру и директору.
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, Wrench } from "lucide-react";
import { post } from "../api";
import { Alert, Button, Field, Modal, useForm, useToast } from "../ui";

export function VehicleStatusButton({ id, plate, status }: { id: number; plate: string; status: string }) {
  const [open, setOpen] = useState(false);
  const toRepair = status === "in_work";                 // кнопка предлагает противоположное нынешнему
  return (
    <>
      <Button icon={toRepair ? Wrench : CircleCheck} onClick={() => setOpen(true)}>{toRepair ? "Машина в ремонте" : "Машина на линии"}</Button>
      {open && <StatusModal id={id} plate={plate} toRepair={toRepair} onClose={() => setOpen(false)} />}
    </>
  );
}

function StatusModal({ id, plate, toRepair, onClose }: { id: number; plate: string; toRepair: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ reason: "" });
  const m = useMutation({
    mutationFn: () => post(`/vehicles/${id}/status`, { status: toRepair ? "in_repair" : "in_work", reason: f.values.reason }),
    onSuccess: () => {
      qc.invalidateQueries();
      toast.success(toRepair ? "Отмечено: машина в ремонте. Механик и диспетчер получили сообщение" : "Отмечено: машина на линии");
      onClose();
    },
    onError: f.fail,
  });
  const submit = () => { f.clear(); m.mutate(); };
  return (
    <Modal title={toRepair ? `${plate}: машина в ремонте` : `${plate}: машина на линии`} onClose={onClose}
      footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={toRepair && !f.values.reason.trim()} onClick={submit}>{toRepair ? "Отметить: в ремонте" : "Да, на линии"}</Button></>}>
      <div className="stack">
        {f.formError && !f.errors.reason && <Alert tone="error">{f.formError}</Alert>}
        {!toRepair && <p>Отметьте, если ремонт закончен и машина снова может работать.</p>}
        <Field label={toRepair ? "Что случилось (обязательно)" : "Заметка (по желанию)"} error={f.errors.reason} hint={toRepair ? "Коротко: что сломалось или что нужно сделать. Это увидят механик и диспетчер" : undefined}>
          <textarea className="input" value={f.values.reason} maxLength={300} autoFocus onChange={(e) => f.set("reason", e.target.value)} />
        </Field>
        {toRepair && <p className="muted small">Нужна деталь или работа? После этого напишите обращение: кнопка «Обращение» на главной.</p>}
      </div>
    </Modal>
  );
}
