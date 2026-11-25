// «Бригады мусоровозов»: директор и диспетчер видят, кто за каким мусоровозом (водитель и грузчики), закрепляют и снимают
// грузчиков и разбирают замечания, которые грузчики присылают с маршрута.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquareWarning, Plus, Recycle, UserMinus, UserRound } from "lucide-react";
import { errorText, get, post } from "../api";
import { fmtDate } from "../format";
import { Alert, Button, Card, ConfirmModal, Empty, Field, FilterChips, Modal, PageHeader, QueryState, VehicleStatus, useForm, useToast } from "../ui";
import { NoteStatus, type RouteNote } from "./LoaderHome";

interface Loader { id: number; employee_id: number; name: string; started_on: string; ended_on: string | null }
interface Truck { id: number; plate: string; label: string; legal_entity_id: number; status: string; status_label: string; driver: string | null; loaders: Loader[]; can_assign: boolean }
interface Person { id: number; name: string; position: string; is_driver: boolean; busy_vehicle_id: number | null; entities: number[] }
interface Crews { today: string; items: Truck[]; people: Person[]; notes: RouteNote[]; open_notes: number; can: { notes: boolean; assign: boolean } }

function AssignModal({ truck, people, trucks, onClose }: { truck: Truck; people: Person[]; trucks: Truck[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ employee_id: "", started_on: "" });
  const plate = (id: number | null) => trucks.find((t) => t.id === id)?.plate;
  const fit = people.filter((x) => x.entities.includes(truck.legal_entity_id) && !truck.loaders.some((l) => l.employee_id === x.id));
  const m = useMutation({
    mutationFn: () => post("/loaders/assign", { vehicle_id: truck.id, employee_id: Number(f.values.employee_id), started_on: f.values.started_on || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["loader-crews"] }); toast.success(`Грузчик закреплён за ${truck.plate}`); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title={`Грузчик на ${truck.plate}`} onClose={onClose}
      footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={!f.values.employee_id} onClick={() => { f.clear(); m.mutate(); }}>Закрепить</Button></>}>
      <div className="stack">
        {f.formError && <Alert tone="error">{f.formError}</Alert>}
        <Field label="Сотрудник" error={f.errors.employee_id} hint="Чтобы грузчик видел свой кабинет, дайте его учётной записи роль «Грузчик мусоровоза»">
          <select className="input" value={f.values.employee_id} onChange={(e) => f.set("employee_id", e.target.value)} autoFocus>
            <option value="">Выберите…</option>
            {fit.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}{x.position ? ` · ${x.position}` : ""}{x.busy_vehicle_id ? ` (сейчас на ${plate(x.busy_vehicle_id) ?? "другой машине"})` : ""}
              </option>
            ))}
          </select>
        </Field>
        <Field label="С какого дня" error={f.errors.started_on} hint="Пусто — с сегодня">
          <input className="input" type="date" value={f.values.started_on} onChange={(e) => f.set("started_on", e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

export function LoaderCrews() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["loader-crews"], queryFn: () => get<Crews>("/loaders/crews"), refetchInterval: 60_000 });
  const [adding, setAdding] = useState<Truck | null>(null);
  const [removing, setRemoving] = useState<{ truck: Truck; l: Loader } | null>(null);
  const [filter, setFilter] = useState<"open" | "all">("open");
  const resolve = useMutation({
    mutationFn: (id: number) => post(`/loaders/notes/${id}/resolve`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["loader-crews"] }); toast.success("Отмечено: разобрались"); },
    onError: (e) => toast.error(errorText(e)),
  });
  const d = q.data;
  const notes = (d?.notes ?? []).filter((n) => filter === "all" || !n.resolved);
  return (
    <>
      <PageHeader title="Бригады мусоровозов" sub="Кто за какой машиной: водитель и грузчики. Здесь же замечания, которые грузчики присылают с маршрута" />
      <QueryState q={q} what="Бригады">
        {d && (
          <div className="stack">
            {d.can.notes && (
              <Card title="Замечания грузчиков" icon={MessageSquareWarning}
                actions={<FilterChips options={[{ id: "open", label: "Ждут", count: d.open_notes }, { id: "all", label: "Все за 2 недели", count: d.notes.length }]} value={filter} onChange={setFilter} />}>
                {notes.length ? (
                  <ul className="plain-list loader-notes">
                    {notes.map((n) => (
                      <li key={n.id} className="loader-note-row">
                        <div className="stack-xs">
                          <div className="row wrap" style={{ gap: 8 }}><strong>{n.plate}: {n.kind_label}</strong><NoteStatus n={n} /></div>
                          <span className="small">{n.street || "адрес не указан"}{n.note ? ` · ${n.note}` : ""}</span>
                          <span className="muted small">{fmtDate(n.date)} · {n.who}</span>
                        </div>
                        {!n.resolved && n.can_resolve && <Button size="sm" loading={resolve.isPending && resolve.variables === n.id} onClick={() => resolve.mutate(n.id)}>Разобрались</Button>}
                      </li>
                    ))}
                  </ul>
                ) : <p className="muted">{filter === "open" ? "Новых замечаний нет." : "За две недели замечаний не было."}</p>}
              </Card>
            )}

            {d.items.length === 0 ? (
              <Card><Empty icon={Recycle} title="Мусоровозов нет">Здесь появятся мусоровозы и машины с галочкой «Вести тоннаж».</Empty></Card>
            ) : (
              <div className="grid cols-2 vehicles-grid">
                {d.items.map((t) => (
                  <Card key={t.id} className="vehicle-home">
                    <div className="job-head">
                      <div className="job-title"><strong className="plate-big">{t.plate}</strong><span className="muted small">{t.label.replace(t.plate + " · ", "")}</span></div>
                      <VehicleStatus code={t.status} label={t.status_label} />
                    </div>
                    <div className="facts">
                      <div><span className="muted small">Водитель</span><strong><UserRound size={14} aria-hidden /> {t.driver || "не закреплён"}</strong></div>
                    </div>
                    <div className="stack-xs">
                      <span className="muted small">Грузчики</span>
                      {t.loaders.length ? (
                        <ul className="plain-list">
                          {t.loaders.map((l) => (
                            <li key={l.id} className="row wrap" style={{ gap: 8, justifyContent: "space-between" }}>
                              <span>{l.name} <span className="muted small">с {fmtDate(l.started_on)}</span></span>
                              {t.can_assign && <Button size="sm" variant="ghost" icon={UserMinus} onClick={() => setRemoving({ truck: t, l })}>Снять</Button>}
                            </li>
                          ))}
                        </ul>
                      ) : <span className="muted">Не закреплены</span>}
                    </div>
                    {t.can_assign && <div><Button size="sm" icon={Plus} onClick={() => setAdding(t)}>Добавить грузчика</Button></div>}
                  </Card>
                ))}
              </div>
            )}
          </div>
        )}
      </QueryState>
      {d && adding && <AssignModal truck={adding} people={d.people} trucks={d.items} onClose={() => setAdding(null)} />}
      {removing && (
        <ConfirmModal title={`Снять ${removing.l.name} с ${removing.truck.plate}?`} text="Грузчик перестанет видеть эту машину и её маршрут. Его прежние замечания останутся."
          confirmLabel="Снять" reasonRequired={false}
          onConfirm={async () => {
            await post(`/loaders/assignments/${removing.l.id}/end`);
            qc.invalidateQueries({ queryKey: ["loader-crews"] });
            toast.success("Грузчик снят с машины");
          }}
          onClose={() => setRemoving(null)} />
      )}
    </>
  );
}
