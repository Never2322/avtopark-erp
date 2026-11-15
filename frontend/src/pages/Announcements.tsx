// Объявления сотрудникам: список, новое объявление, карточка с тем, кто прочитал и кто ответил, переписка с каждым адресатом.
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, CheckCheck, Lock, LockOpen, Megaphone, MessageSquare, Plus, Send } from "lucide-react";
import { ApiError, errorText, get, post } from "../api";
import { useAuth } from "../auth";
import { useEntityParam, useUrlState } from "../hooks";
import { fmtDateTime } from "../format";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, Modal, NotFound, PageHeader, Pagination, QueryState, Table, Tabs, useForm, useToast } from "../ui";
import { useNotifSummary } from "../components/Bell";

interface Row {
  id: number; title: string; author: string; created_at: string; closed: boolean; archived: boolean; archive_reason: string; audience_label: string;
  legal_entity: string; legal_entity_id: number; total: number; seen: number; answers: number; waiting: number; can_edit: boolean; can_void: boolean;
}
interface Listing { items: Row[]; total: number; page: number; page_size: number; can_create: boolean }
interface Msg { id: number; author: string; from_staff: boolean; text: string; created_at: string }
interface Person { user_id: number; name: string; read_at: string | null; waiting: boolean; thread: Msg[] }
interface Card_ extends Omit<Row, "legal_entity" | "total" | "seen" | "answers" | "waiting"> { body: string; audience: string; people: Person[] }
interface Audience { people: { user_id: number; name: string; is_driver: boolean; position: string }[]; audiences: { value: string; label: string }[] }

const SIZE = 30;
const refreshAll = (qc: ReturnType<typeof useQueryClient>) => {
  qc.invalidateQueries({ queryKey: ["announcements"] });
  qc.invalidateQueries({ queryKey: ["notif-summary"] });
  qc.invalidateQueries({ queryKey: ["notifications"] });
};

export function Announcements() {
  const { can } = useAuth();
  const nav = useNavigate();
  const entity = useEntityParam();
  const [f, setF] = useUrlState({ tab: "active", q: "", page: "1" });
  const [creating, setCreating] = useState(false);
  const page = Number(f.page) || 1;
  const archived = f.tab === "archive";
  const params = { archived, q: f.q, legal_entity_id: entity, page, page_size: SIZE };
  const q = useQuery({ queryKey: ["announcements", "list", params], queryFn: () => get<Listing>("/announcements", params), placeholderData: (p) => p, refetchInterval: 60_000 });
  const sum = useNotifSummary();
  const d = q.data;
  const news = sum.data?.news_unread ?? 0;
  return (
    <>
      <PageHeader title="Объявления" sub="Сообщение водителям или сотрудникам с входом в программу. Люди видят его в колокольчике и в «Моих объявлениях» и отвечают прямо на сайте"
        actions={<>
          {can("announcements.read_own") && <Link className="btn" to="/news"><MessageSquare size={16} aria-hidden /> Адресованные мне{news > 0 ? ` (${news})` : ""}</Link>}
          {d?.can_create && <Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>Новое объявление</Button>}
        </>} />
      <Tabs value={f.tab} onChange={(v) => setF({ tab: v })} tabs={[{ id: "active", label: "Действующие" }, { id: "archive", label: "Архив" }]} />
      <div className="toolbar">
        <input className="input" style={{ maxWidth: 320 }} placeholder="Найти по заголовку" aria-label="Поиск по заголовку" value={f.q} onChange={(e) => setF({ q: e.target.value })} />
      </div>
      <QueryState q={q}>
        {d && (d.items.length === 0
          ? <Empty icon={Megaphone} title={archived ? "В архиве пусто" : f.q ? "Ничего не найдено" : "Объявлений пока нет"}>
              {archived ? "Сюда попадают убранные в архив объявления." : d.can_create ? "Нажмите «Новое объявление»: например, о выходе на смену, графике или изменении правил." : "Когда появится первое объявление, оно будет здесь."}
            </Empty>
          : <>
              <Table rows={d.items} rowKey={(r) => r.id} dim={(r) => r.closed || r.archived} onRowClick={(r) => nav(`/announcements/${r.id}`)}
                columns={[
                  { key: "t", header: "Заголовок", render: (r) => (
                    <>
                      <Link to={`/announcements/${r.id}`} onClick={(e) => e.stopPropagation()}><strong>{r.title}</strong></Link>
                      <span className="only-sm muted small">{r.author} · {fmtDateTime(r.created_at)}</span>
                    </>
                  ) },
                  { key: "a", header: "Кому", hideSm: true, render: (r) => <><div>{r.audience_label}</div><span className="muted small">{r.legal_entity}</span></> },
                  { key: "s", header: "Прочитали", render: (r) => <span className="nowrap">{r.seen} из {r.total}</span> },
                  { key: "r", header: "Ответы", render: (r) => (
                    r.answers === 0 ? <span className="muted">нет</span>
                      : <span className="row wrap" style={{ gap: 6 }}><span>{r.answers}</span>{r.waiting > 0 && <Badge tone="warning">ждут ответа: {r.waiting}</Badge>}</span>
                  ) },
                  { key: "c", header: "Состояние", render: (r) => (r.archived ? <Badge>В архиве</Badge> : r.closed ? <Badge>Закрыто</Badge> : <Badge tone="success">Действует</Badge>) },
                  { key: "d", header: "Написано", hideSm: true, render: (r) => <><div className="nowrap">{fmtDateTime(r.created_at)}</div><span className="muted small">{r.author}</span></> },
                ]} />
              <Pagination page={page} pageSize={SIZE} total={d.total} onPage={(p) => setF({ page: String(p) })} />
            </>)}
      </QueryState>
      {creating && <NewAnnouncement onClose={() => setCreating(false)} onDone={(id) => { setCreating(false); nav(`/announcements/${id}`); }} />}
    </>
  );
}

function NewAnnouncement({ onClose, onDone }: { onClose: () => void; onDone: (id: number) => void }) {
  const { me, can, entityId } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const options = me!.legal_entities.filter((e) => can("announcements.create", e.id));
  const f = useForm({ legal_entity_id: String(entityId && options.some((e) => e.id === entityId) ? entityId : options.length === 1 ? options[0].id : ""), title: "", body: "", audience: "drivers", user_ids: [] as number[] });
  const eid = Number(f.values.legal_entity_id) || 0;
  const aud = useQuery({ queryKey: ["announcements", "audience", eid], queryFn: () => get<Audience>("/announcements/audience", { legal_entity_id: eid }), enabled: eid > 0 });
  const [filter, setFilter] = useState("");
  const [top, setTop] = useState("");                                // общая ошибка: не про конкретное поле (или про скрытое)
  const people = (aud.data?.people ?? []).filter((p) => p.name.toLowerCase().includes(filter.trim().toLowerCase()));
  const drivers = (aud.data?.people ?? []).filter((p) => p.is_driver).length;
  const all = aud.data?.people.length ?? 0;
  const send = useMutation({
    mutationFn: () => post<{ id: number; recipients: number }>("/announcements", { ...f.values, legal_entity_id: eid }),
    onSuccess: (r) => { refreshAll(qc); toast.success(`Объявление отправлено: получателей ${r.recipients}`); onDone(r.id); },
    onError: (e) => {
      f.fail(e);
      const own = e instanceof ApiError && (e.fields || e.field) && !(e.field === "legal_entity_id" && options.length <= 1);
      setTop(own ? "" : errorText(e));
    },
  });
  const toggle = (id: number) => f.set("user_ids", f.values.user_ids.includes(id) ? f.values.user_ids.filter((x) => x !== id) : [...f.values.user_ids, id]);
  const hint = f.values.audience === "drivers" ? `Получат водители со входом в программу: ${drivers}` : f.values.audience === "all" ? `Получат все сотрудники со входом в программу: ${all}` : `Отмечено: ${f.values.user_ids.length}`;
  return (
    <Modal title="Новое объявление" onClose={onClose} wide footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" icon={Send} loading={send.isPending} onClick={() => { f.clear(); setTop(""); send.mutate(); }}>Отправить</Button>
    </>}>
      <div className="stack-sm">
        {top && <Alert tone="error">{top}</Alert>}
        {options.length > 1 && (
          <Field label="Юридическое лицо" error={f.errors.legal_entity_id}>
            <select className="input" value={f.values.legal_entity_id} onChange={(e) => { f.set("legal_entity_id", e.target.value); f.set("user_ids", []); }}>
              <option value="">Выберите</option>
              {options.map((e) => <option key={e.id} value={e.id}>{e.short_name}</option>)}
            </select>
          </Field>
        )}
        <Field label="Заголовок" error={f.errors.title}>
          <input className="input" value={f.values.title} maxLength={120} onChange={(e) => f.set("title", e.target.value)} placeholder="Например: выход на вывоз снега в субботу" />
        </Field>
        <Field label="Текст" error={f.errors.body} hint="Можно оставить пустым, если хватает заголовка">
          <textarea className="input" style={{ minHeight: 120 }} maxLength={3000} value={f.values.body} onChange={(e) => f.set("body", e.target.value)} />
        </Field>
        <fieldset className="radio-set">
          <legend className="label">Кому</legend>
          {(aud.data?.audiences ?? [{ value: "drivers", label: "Всем водителям" }, { value: "all", label: "Всем сотрудникам с входом" }, { value: "pick", label: "Выбранным" }]).map((a) => (
            <label key={a.value} className="radio"><input type="radio" name="aud" checked={f.values.audience === a.value} onChange={() => f.set("audience", a.value)} /> {a.label}</label>
          ))}
          <span className="hint">{eid ? hint : "Сначала выберите юридическое лицо"}</span>
          {f.errors.audience && <span className="err" role="alert">{f.errors.audience}</span>}
        </fieldset>
        {f.values.audience === "pick" && eid > 0 && (
          <div className="stack-sm">
            <input className="input" placeholder="Найти человека" aria-label="Найти человека" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <div className="pick-list" role="group" aria-label="Получатели">
              {people.length === 0 && <div className="muted small" style={{ padding: 10 }}>{aud.isLoading ? "Загрузка…" : "Некого выбрать: у сотрудников нет входа в программу или они не работают в этом юрлице."}</div>}
              {people.map((p) => (
                <label key={p.user_id} className="pick">
                  <input type="checkbox" checked={f.values.user_ids.includes(p.user_id)} onChange={() => toggle(p.user_id)} />
                  <span>{p.name}{p.position ? <span className="muted"> · {p.position}</span> : null}</span>
                </label>
              ))}
            </div>
            {f.errors.user_ids && <span className="err" role="alert">{f.errors.user_ids}</span>}
          </div>
        )}
      </div>
    </Modal>
  );
}

export function AnnouncementCard() {
  const { id } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const [archive, setArchive] = useState(false);
  const q = useQuery({ queryKey: ["announcements", "card", id], queryFn: () => get<Card_>(`/announcements/${id}`), refetchInterval: 30_000, retry: (n, e) => (e as { status?: number })?.status !== 404 && n < 2 });
  const a = q.data;
  const act = useMutation({
    mutationFn: (path: string) => post(`/announcements/${id}/${path}`),
    onSuccess: () => { refreshAll(qc); toast.success("Готово"); },
    onError: (e) => toast.error(errorText(e)),
  });
  if ((q.error as { status?: number } | null)?.status === 404) return <NotFound what="Объявление" text="Оно удалено из вашего доступа или адрес неверный." />;
  const seen = a?.people.filter((p) => p.read_at).length ?? 0;
  const waiting = a?.people.filter((p) => p.waiting).length ?? 0;
  return (
    <QueryState q={q}>
      {a && (
        <>
          <PageHeader crumb={<Link to="/announcements">← Объявления</Link>} title={a.title}
            sub={<>{a.author} · {fmtDateTime(a.created_at)} · {a.audience_label}</>}
            actions={<>
              {a.archived
                ? a.can_void && <Button icon={ArchiveRestore} loading={act.isPending} onClick={() => act.mutate("restore")}>Вернуть из архива</Button>
                : <>
                    {a.can_edit && (a.closed
                      ? <Button icon={LockOpen} loading={act.isPending} onClick={() => act.mutate("reopen")}>Открыть снова</Button>
                      : <Button icon={Lock} loading={act.isPending} onClick={() => act.mutate("close")}>Закрыть</Button>)}
                    {a.can_void && <Button icon={Archive} onClick={() => setArchive(true)}>В архив</Button>}
                  </>}
            </>} />
          {a.archived && <Alert tone="info">Объявление в архиве{a.archive_reason ? `: ${a.archive_reason}` : ""}. Адресаты его не видят.</Alert>}
          {a.closed && !a.archived && <Alert tone="info">Объявление закрыто: адресаты его больше не видят. Переписка сохранена, её можно открыть снова кнопкой «Открыть снова».</Alert>}
          <div className="grid cols-2" style={{ marginTop: 16, alignItems: "start" }}>
            <Card title="Текст">
              {a.body ? <div style={{ whiteSpace: "pre-wrap" }}>{a.body}</div> : <span className="muted">Только заголовок, без текста.</span>}
              <div className="row wrap" style={{ marginTop: 14 }}>
                <Badge tone="info"><CheckCheck size={12} aria-hidden /> Прочитали: {seen} из {a.people.length}</Badge>
                {waiting > 0 && <Badge tone="warning">Ждут ответа: {waiting}</Badge>}
              </div>
            </Card>
            <Card title="Адресаты и переписка">
              {a.people.length === 0 ? <span className="muted">Адресатов нет.</span> : (
                <div className="stack">
                  {a.people.map((p) => <PersonThread key={p.user_id} aid={a.id} p={p} canAnswer={a.can_edit && !a.archived} />)}
                </div>
              )}
            </Card>
          </div>
          {archive && (
            <ConfirmModal title="Убрать объявление в архив" confirmLabel="В архив" danger reasonLabel="Причина"
              text="Адресаты перестанут его видеть. Из архива его можно вернуть."
              onConfirm={async (reason) => { await post(`/announcements/${a.id}/void`, { reason }); refreshAll(qc); }} onClose={() => setArchive(false)} />
          )}
        </>
      )}
    </QueryState>
  );
}

function PersonThread({ aid, p, canAnswer }: { aid: number; p: Person; canAnswer: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(p.waiting);
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: () => post(`/announcements/${aid}/answer/${p.user_id}`, { text }),
    onSuccess: () => { setText(""); refreshAll(qc); },
    onError: (e) => toast.error(errorText(e)),
  });
  return (
    <div className={`person ${p.waiting ? "waiting" : ""}`}>
      <button className="person-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <strong>{p.name}</strong>
        {p.waiting && <Badge tone="warning">ждёт ответа</Badge>}
        <span className="spacer" />
        <span className="muted small">{p.read_at ? `прочитал ${fmtDateTime(p.read_at)}` : "ещё не открывал"}{p.thread.length ? ` · сообщений ${p.thread.length}` : ""}</span>
      </button>
      {open && (
        <div className="stack-sm" style={{ marginTop: 10 }}>
          {p.thread.length === 0 && <span className="muted small">Ответов пока нет.</span>}
          {p.thread.map((m) => (
            <div key={m.id} className={`bubble ${m.from_staff ? "staff" : ""}`}>
              <div style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>
              <div className="muted small">{m.author} · {fmtDateTime(m.created_at)}</div>
            </div>
          ))}
          {canAnswer && (
            <form className="row" style={{ alignItems: "flex-start" }} onSubmit={(e) => { e.preventDefault(); if (text.trim()) send.mutate(); }}>
              <textarea className="input" style={{ minHeight: 44 }} aria-label={`Ответ для ${p.name}`} placeholder="Написать ответ" maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} />
              <Button type="submit" variant="primary" icon={Send} loading={send.isPending} disabled={!text.trim()} aria-label="Отправить ответ" />
            </form>
          )}
        </div>
      )}
    </div>
  );
}
