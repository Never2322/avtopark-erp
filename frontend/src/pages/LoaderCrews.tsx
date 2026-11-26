// «Бригады мусоровозов»: директор и диспетчер видят, кто за каким мусоровозом (водитель и грузчики), закрепляют и снимают
// грузчиков, выбирают, какие машины здесь показывать, и разбирают замечания, которые грузчики присылают с маршрута.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, MessageSquareWarning, Plus, Recycle, Search, Trash2, Truck, UserMinus, UserPlus, Users } from "lucide-react";
import { errorText, get, post } from "../api";
import { fmtDate, plural } from "../format";
import { Alert, Button, Card, ConfirmModal, Empty, Field, FilterChips, Modal, PageHeader, QueryState, VehicleStatus, useForm, useToast } from "../ui";
import { initials, NoteStatus, type RouteNote } from "./LoaderHome";

interface Loader { id: number; employee_id: number; name: string; started_on: string; ended_on: string | null }
interface Truck { id: number; plate: string; label: string; legal_entity_id: number; status: string; status_label: string; driver: string | null; loaders: Loader[]; can_assign: boolean }
interface Person { id: number; name: string; position: string; is_driver: boolean; busy_vehicle_id: number | null; entities: number[] }
interface Other { id: number; plate: string; label: string; legal_entity_id: number }
interface Crews { today: string; items: Truck[]; people: Person[]; notes: RouteNote[]; open_notes: number; others: Other[]; can: { notes: boolean; assign: boolean } }

const NOTE_TONE: Record<string, string> = { not_taken: "error", overflow: "warning", broken: "warning", blocked: "info", other: "muted" };


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

/** Добавить в бригады машину из остальной техники. */
function AddVehicleModal({ others, onClose }: { others: Other[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [q, setQ] = useState("");
  const add = useMutation({
    mutationFn: (v: Other) => post(`/loaders/vehicles/${v.id}`, { in_crews: true }).then(() => v),
    onSuccess: (v) => { qc.invalidateQueries({ queryKey: ["loader-crews"] }); toast.success(`${v.plate} теперь в бригадах`); },
    onError: (e) => toast.error(errorText(e)),
  });
  const s = q.trim().toLowerCase();
  const list = others.filter((v) => !s || v.label.toLowerCase().includes(s));
  return (
    <Modal title="Добавить машину в бригады" onClose={onClose} footer={<Button onClick={onClose}>Готово</Button>}>
      <div className="stack">
        <div className="crew-search">
          <Search size={16} aria-hidden />
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Номер, марка или модель" autoFocus />
        </div>
        {list.length ? (
          <ul className="plain-list crew-pick">
            {list.map((v) => (
              <li key={v.id}>
                <span className="crew-pick-name"><span className="plate-tag">{v.plate}</span><span className="muted small">{v.label.replace(v.plate + " · ", "")}</span></span>
                <Button size="sm" icon={Plus} loading={add.isPending && add.variables?.id === v.id} onClick={() => add.mutate(v)}>Добавить</Button>
              </li>
            ))}
          </ul>
        ) : <p className="muted">{others.length ? "Ничего не нашлось." : "Вся техника уже в бригадах."}</p>}
      </div>
    </Modal>
  );
}

function HeroTile({ icon: Icon, title, value, sub, tone }: { icon: typeof Truck; title: string; value: number; sub: string; tone: "mint" | "blue" | "amber" | "rose" }) {
  return (
    <div className={`card deco crew-tile tone-${tone}`}>
      <span className="crew-tile-ico" aria-hidden><Icon size={20} /></span>
      <div className="deco-body">
        <span className="deco-title">{title}</span>
        <span className="deco-big">{value}</span>
        <span className="deco-sub">{sub}</span>
      </div>
    </div>
  );
}

export function LoaderCrews() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["loader-crews"], queryFn: () => get<Crews>("/loaders/crews"), refetchInterval: 60_000 });
  const [adding, setAdding] = useState<Truck | null>(null);
  const [addingVehicle, setAddingVehicle] = useState(false);
  const [removing, setRemoving] = useState<{ truck: Truck; l: Loader } | null>(null);
  const [dropping, setDropping] = useState<Truck | null>(null);
  const [filter, setFilter] = useState<"open" | "all">("open");
  const resolve = useMutation({
    mutationFn: (id: number) => post(`/loaders/notes/${id}/resolve`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["loader-crews"] }); toast.success("Отмечено: разобрались"); },
    onError: (e) => toast.error(errorText(e)),
  });
  const d = q.data;
  const notes = (d?.notes ?? []).filter((n) => filter === "all" || !n.resolved);
  const loaders = d ? d.items.reduce((s, t) => s + t.loaders.length, 0) : 0;
  const empty = d ? d.items.filter((t) => t.loaders.length === 0).length : 0;
  return (
    <>
      <PageHeader title="Бригады мусоровозов" sub="Кто за какой машиной: водитель и грузчики. Здесь же замечания, которые грузчики присылают с маршрута"
        actions={d?.can.assign ? <Button icon={Plus} onClick={() => setAddingVehicle(true)}>Добавить машину</Button> : undefined} />
      <QueryState q={q} what="Бригады">
        {d && (
          <div className="stack">
            <div className="crew-hero">
              <HeroTile icon={Truck} tone="mint" title="Машин в бригадах" value={d.items.length} sub={empty ? `без грузчиков: ${empty}` : "у всех есть грузчики"} />
              <HeroTile icon={Users} tone="blue" title="Грузчиков на машинах" value={loaders} sub={d.items.length ? `в среднем ${(loaders / d.items.length).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} на машину` : "машин пока нет"} />
              {d.can.notes && <HeroTile icon={MessageSquareWarning} tone={d.open_notes ? "amber" : "mint"} title="Замечания ждут" value={d.open_notes} sub={d.open_notes ? "нужно разобраться" : "всё разобрано"} />}
            </div>

            {d.can.notes && (
              <Card title="Замечания грузчиков" icon={MessageSquareWarning} className="crew-notes-card"
                actions={<FilterChips options={[{ id: "open", label: "Ждут", count: d.open_notes }, { id: "all", label: "Все за 2 недели", count: d.notes.length }]} value={filter} onChange={setFilter} />}>
                {notes.length ? (
                  <ul className="plain-list loader-notes">
                    {notes.map((n) => (
                      <li key={n.id} className={`loader-note-row note-${NOTE_TONE[n.kind] ?? "muted"}${n.resolved ? " is-done" : ""}`}>
                        <div className="stack-xs">
                          <div className="row wrap" style={{ gap: 8 }}><span className="plate-tag sm">{n.plate}</span><strong>{n.kind_label}</strong><NoteStatus n={n} /></div>
                          <span className="small">{n.street || "адрес не указан"}{n.note ? ` · ${n.note}` : ""}</span>
                          <span className="muted small">{fmtDate(n.date)} · {n.who}</span>
                        </div>
                        {!n.resolved && n.can_resolve && <Button size="sm" icon={CheckCircle2} loading={resolve.isPending && resolve.variables === n.id} onClick={() => resolve.mutate(n.id)}>Разобрались</Button>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="crew-calm"><CheckCircle2 size={22} aria-hidden /><span>{filter === "open" ? "Новых замечаний нет. Маршруты идут спокойно." : "За две недели замечаний не было."}</span></div>
                )}
              </Card>
            )}

            {d.items.length === 0 ? (
              <Card><Empty icon={Recycle} title="В бригадах нет машин">Нажмите «Добавить машину» и выберите мусоровозы, на которых работают грузчики.</Empty></Card>
            ) : (
              <div className="grid cols-2 vehicles-grid">
                {d.items.map((t) => (
                  <section key={t.id} className={`card crew-card${t.loaders.length ? "" : " is-empty"}`}>
                    <div className="crew-card-head">
                      <span className="crew-truck-ico" aria-hidden><Recycle size={20} /></span>
                      <div className="job-title"><span className="plate-tag">{t.plate}</span><span className="muted small">{t.label.replace(t.plate + " · ", "")}</span></div>
                      <VehicleStatus code={t.status} label={t.status_label} />
                    </div>
                    <ul className="plain-list crew-people">
                      <li className="crew-person is-driver">
                        <span className="crew-ava drv" aria-hidden>{t.driver ? initials(t.driver) : "?"}</span>
                        <span className="crew-person-name"><strong>{t.driver || "Водитель не закреплён"}</strong><span className="muted small">водитель</span></span>
                      </li>
                      {t.loaders.map((l) => (
                        <li key={l.id} className="crew-person">
                          <span className="crew-ava" aria-hidden>{initials(l.name)}</span>
                          <span className="crew-person-name"><strong>{l.name}</strong><span className="muted small">грузчик · с {fmtDate(l.started_on)}</span></span>
                          {t.can_assign && <Button size="sm" variant="ghost" icon={UserMinus} onClick={() => setRemoving({ truck: t, l })}>Снять</Button>}
                        </li>
                      ))}
                      {t.loaders.length === 0 && <li className="crew-person muted small">Грузчики не закреплены</li>}
                    </ul>
                    {t.can_assign && (
                      <div className="crew-card-foot">
                        <Button size="sm" icon={UserPlus} onClick={() => setAdding(t)}>Добавить грузчика</Button>
                        <Button size="sm" variant="ghost" icon={Trash2} onClick={() => setDropping(t)}>Убрать из бригад</Button>
                      </div>
                    )}
                  </section>
                ))}
              </div>
            )}
          </div>
        )}
      </QueryState>
      {d && adding && <AssignModal truck={adding} people={d.people} trucks={d.items} onClose={() => setAdding(null)} />}
      {d && addingVehicle && <AddVehicleModal others={d.others} onClose={() => setAddingVehicle(false)} />}
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
      {dropping && (
        <ConfirmModal title={`Убрать ${dropping.plate} из бригад?`} danger
          text={<>Машина пропадёт с этой страницы. Сама машина, её талоны и маршруты не удаляются, вернуть её можно кнопкой «Добавить машину».
            {dropping.loaders.length > 0 && <> С неё будут сняты {dropping.loaders.length} {plural(dropping.loaders.length, "грузчик", "грузчика", "грузчиков")}.</>}</>}
          confirmLabel="Убрать" reasonRequired={false}
          onConfirm={async () => {
            await post(`/loaders/vehicles/${dropping.id}`, { in_crews: false });
            qc.invalidateQueries({ queryKey: ["loader-crews"] });
            toast.success(`${dropping.plate} убрана из бригад`);
          }}
          onClose={() => setDropping(null)} />
      )}
    </>
  );
}
