import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Plus, RotateCcw, Search, UserPlus, Users as UsersIcon } from "lucide-react";
import { get, patch, post } from "../api";
import { useAuth } from "../auth";
import { fmtDate, todayIso } from "../format";
import { Page, useDebounced, useEntityParam, useUrlState } from "../hooks";
import { EmployeeEvents } from "../components/EmployeeEvents";
import { EmployeePpe } from "../components/EmployeePpe";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, FilterChips, Modal, PageHeader, Pagination, QueryState, Table, useForm, useToast } from "../ui";

interface Employee {
  id: number; full_name: string; last_name: string; first_name: string; middle_name: string; position: string; is_driver: boolean; department_id: number | null;
  hired_on: string | null; fired_on: string | null; archived: boolean; user_id: number | null; legal_entities: { id: number; name: string }[];
  sensitive_hidden: boolean; phone: string | null; email: string | null; birth_date: string | null;
  note?: string; user_login?: string | null; vehicles?: { id: number; plate: string; name: string }[];
  assignments?: { id: number; legal_entity_id: number; legal_entity: string; position: string; started_on: string | null; ended_on: string | null }[];
}

const nul = (s: string) => (s.trim() === "" ? null : s);

function useEntityChoices(perm: string) {
  const { me, can } = useAuth();
  return me!.legal_entities.filter((e) => can(perm, e.id));
}

function EmployeeForm({ initial, onClose }: { initial?: Employee; onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const entities = useEntityChoices(initial ? "employees.edit" : "employees.create");
  const f = useForm({
    last_name: initial?.last_name ?? "", first_name: initial?.first_name ?? "", middle_name: initial?.middle_name ?? "", position: initial?.position ?? "",
    is_driver: initial?.is_driver ?? false, phone: initial?.phone ?? "", email: initial?.email ?? "", birth_date: initial?.birth_date ?? "",
    hired_on: initial?.hired_on ?? "", fired_on: initial?.fired_on ?? "", note: initial?.note ?? "", department_id: initial?.department_id ? String(initial.department_id) : "",
    legal_entity_id: String(entities[0]?.id ?? ""), started_on: todayIso(),
  });
  const entityForDepartments = initial?.legal_entities[0]?.id ?? Number(f.values.legal_entity_id);
  const deps = useQuery({
    queryKey: ["departments", entityForDepartments], enabled: !!entityForDepartments && can("legal_entities.view", entityForDepartments), retry: false,
    queryFn: () => get<{ items: { id: number; name: string }[] }>(`/legal-entities/${entityForDepartments}/departments`),
  });
  const m = useMutation({
    mutationFn: () => {
      const v = f.values;
      const body: Record<string, unknown> = {
        last_name: v.last_name, first_name: v.first_name, middle_name: v.middle_name, position: v.position, is_driver: v.is_driver, phone: v.phone, email: v.email,
        birth_date: nul(v.birth_date), hired_on: nul(v.hired_on), fired_on: nul(v.fired_on), note: v.note, department_id: v.department_id ? Number(v.department_id) : null,
      };
      if (initial) return patch(`/employees/${initial.id}`, body);
      return post("/employees", { ...body, assignments: [{ legal_entity_id: Number(v.legal_entity_id), position: v.position, started_on: nul(v.started_on) }] });
    },
    onSuccess: () => { qc.invalidateQueries(); toast.success(initial ? "Сохранено" : "Сотрудник добавлен"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title={initial ? "Изменить сотрудника" : "Добавить сотрудника"} wide onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" loading={m.isPending} disabled={!f.values.last_name.trim() || (!initial && !f.values.legal_entity_id)} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button>
    </>}>
      <div className="stack">
        {f.formError && !f.errors.last_name && !f.errors.assignments && <Alert tone="error">{f.formError}</Alert>}
        {f.errors.assignments && <Alert tone="error">{f.errors.assignments}</Alert>}
        <div className="form-grid">
          <Field label="Фамилия" error={f.errors.last_name}><input className="input" value={f.values.last_name} maxLength={80} onChange={(e) => f.set("last_name", e.target.value)} /></Field>
          <Field label="Имя" error={f.errors.first_name}><input className="input" value={f.values.first_name} maxLength={80} onChange={(e) => f.set("first_name", e.target.value)} /></Field>
          <Field label="Отчество" error={f.errors.middle_name}><input className="input" value={f.values.middle_name} maxLength={80} onChange={(e) => f.set("middle_name", e.target.value)} /></Field>
          <Field label="Должность" error={f.errors.position}><input className="input" value={f.values.position} maxLength={120} onChange={(e) => f.set("position", e.target.value)} /></Field>
          {!initial && (
            <Field label="Где работает" error={f.errors.legal_entity_id} hint="Юридическое лицо. Дополнительные места работы добавляются потом в карточке">
              <select className="input" value={f.values.legal_entity_id} onChange={(e) => f.set("legal_entity_id", e.target.value)}>{entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select>
            </Field>
          )}
          {deps.data && deps.data.items.length > 0 && (
            <Field label="Подразделение" error={f.errors.department_id}>
              <select className="input" value={f.values.department_id} onChange={(e) => f.set("department_id", e.target.value)}><option value="">Не указано</option>{deps.data.items.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
            </Field>
          )}
          <Field label="Принят" error={f.errors.hired_on}><input className="input" type="date" value={f.values.hired_on} onChange={(e) => f.set("hired_on", e.target.value)} /></Field>
          <Field label="Уволен" error={f.errors.fired_on}><input className="input" type="date" value={f.values.fired_on} onChange={(e) => f.set("fired_on", e.target.value)} /></Field>
          {!(initial?.sensitive_hidden) && <>
            <Field label="Телефон" error={f.errors.phone}><input className="input" inputMode="tel" value={f.values.phone} maxLength={40} onChange={(e) => f.set("phone", e.target.value)} /></Field>
            <Field label="Почта" error={f.errors.email}><input className="input" type="email" value={f.values.email} maxLength={120} onChange={(e) => f.set("email", e.target.value)} /></Field>
            <Field label="Дата рождения" error={f.errors.birth_date}><input className="input" type="date" value={f.values.birth_date} onChange={(e) => f.set("birth_date", e.target.value)} /></Field>
          </>}
          <Field label=" " full><label className="check"><input type="checkbox" checked={f.values.is_driver} onChange={(e) => f.set("is_driver", e.target.checked)} /> Водитель (его можно закреплять за техникой и указывать в путевых листах)</label></Field>
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} maxLength={2000} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
      </div>
    </Modal>
  );
}

function AssignmentForm({ e, onClose }: { e: Employee; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const entities = useEntityChoices("employees.edit").filter((x) => !e.assignments?.some((a) => a.legal_entity_id === x.id && !a.ended_on));
  const f = useForm({ legal_entity_id: String(entities[0]?.id ?? ""), position: e.position, started_on: todayIso() });
  const m = useMutation({
    mutationFn: () => post(`/employees/${e.id}/assignments`, { legal_entity_id: Number(f.values.legal_entity_id), position: f.values.position, started_on: nul(f.values.started_on) }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Место работы добавлено"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Принять в юрлицо" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={!f.values.legal_entity_id} onClick={() => { f.clear(); m.mutate(); }}>Добавить</Button></>}>
      <div className="stack">
        {entities.length === 0 && <Alert tone="info">Сотрудник уже работает во всех юрлицах, доступных вам.</Alert>}
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <Field label="Юридическое лицо" error={f.errors.legal_entity_id}><select className="input" value={f.values.legal_entity_id} onChange={(x) => f.set("legal_entity_id", x.target.value)}>{entities.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        <Field label="Должность" error={f.errors.position}><input className="input" value={f.values.position} onChange={(x) => f.set("position", x.target.value)} /></Field>
        <Field label="С какой даты" error={f.errors.started_on}><input className="input" type="date" value={f.values.started_on} onChange={(x) => f.set("started_on", x.target.value)} /></Field>
      </div>
    </Modal>
  );
}

function EndAssignment({ e, aid, onClose }: { e: Employee; aid: number; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ ended_on: todayIso() });
  const m = useMutation({
    mutationFn: () => post(`/employees/${e.id}/assignments/${aid}/end`, { ended_on: f.values.ended_on }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Работа в юрлице закончена"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Закончить работу в юрлице" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Закончить</Button></>}>
      <Field label="Последний день" error={f.errors.ended_on}><input className="input" type="date" value={f.values.ended_on} onChange={(x) => f.set("ended_on", x.target.value)} /></Field>
      {f.formError && !f.errors.ended_on && <Alert tone="error">{f.formError}</Alert>}
    </Modal>
  );
}

function EmployeeDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [sub, setSub] = useState<null | "edit" | "assign" | "archive" | { end: number }>(null);
  const q = useQuery({ queryKey: ["employee", id], queryFn: () => get<Employee>(`/employees/${id}`) });
  const restore = useMutation({ mutationFn: () => post(`/employees/${id}/restore`), onSuccess: () => { qc.invalidateQueries(); toast.success("Сотрудник возвращён"); }, onError: (e: any) => toast.error(e.message) });
  const archive = useMutation({ mutationFn: () => post(`/employees/${id}/archive`), onSuccess: () => qc.invalidateQueries() });
  const e = q.data;
  const canEdit = !!e && e.legal_entities.some((x) => can("employees.edit", x.id)) ;
  const canArchive = !!e && e.legal_entities.some((x) => can("employees.archive", x.id));
  return (
    <Modal title={e ? e.full_name : "Сотрудник"} wide onClose={onClose} footer={e && <>
      {canArchive && (e.archived ? <Button icon={RotateCcw} loading={restore.isPending} onClick={() => restore.mutate()}>Вернуть из архива</Button> : <Button variant="danger" icon={Archive} onClick={() => setSub("archive")}>В архив</Button>)}
      <span className="spacer" />
      {canEdit && !e.archived && <Button icon={UserPlus} onClick={() => setSub("assign")}>Принять в юрлицо</Button>}
      {canEdit && !e.archived && <Button variant="primary" onClick={() => setSub("edit")}>Изменить</Button>}
    </>}>
      <QueryState q={q}>
        {e && (
          <div className="stack">
            <div className="row wrap">{e.is_driver && <Badge tone="info">Водитель</Badge>}{e.archived && <Badge>В архиве</Badge>}{e.user_login && <Badge tone="mint">Вход: {e.user_login}</Badge>}</div>
            <dl className="kv">
              <dt>Должность</dt><dd>{e.position || "—"}</dd>
              <dt>Телефон</dt><dd>{e.sensitive_hidden ? <span className="muted">скрыт</span> : e.phone || "—"}</dd>
              <dt>Почта</dt><dd>{e.sensitive_hidden ? <span className="muted">скрыта</span> : e.email || "—"}</dd>
              <dt>Дата рождения</dt><dd>{e.sensitive_hidden ? <span className="muted">скрыта</span> : fmtDate(e.birth_date)}</dd>
              <dt>Принят / уволен</dt><dd>{fmtDate(e.hired_on)} / {e.fired_on ? fmtDate(e.fired_on) : "—"}</dd>
              <dt>Закреплена техника</dt><dd>{e.vehicles?.length ? e.vehicles.map((v) => v.plate).join(", ") : <span className="muted">нет</span>}</dd>
              <dt>Заметка</dt><dd style={{ whiteSpace: "pre-wrap" }}>{e.note || <span className="muted">нет</span>}</dd>
            </dl>
            {can("employee_events.view") && <EmployeeEvents employeeId={e.id} entityIds={e.legal_entities.map((x) => x.id)} />}
            {can("ppe.view") && <EmployeePpe employeeId={e.id} />}
            <h3 style={{ margin: "6px 0 0", fontSize: 14 }}>Где работает</h3>
            <Table rows={e.assignments ?? []} rowKey={(a) => a.id}
              columns={[
                { key: "e", header: "Юрлицо", render: (a) => a.legal_entity },
                { key: "p", header: "Должность", render: (a) => a.position || "—" },
                { key: "s", header: "С", render: (a) => fmtDate(a.started_on) },
                { key: "d", header: "По", render: (a) => (a.ended_on ? fmtDate(a.ended_on) : <Badge tone="success">работает</Badge>) },
                { key: "x", header: "", render: (a) => !a.ended_on && canEdit && !e.archived && can("employees.edit", a.legal_entity_id) ? <Button size="sm" onClick={() => setSub({ end: a.id })}>Закончить</Button> : null },
              ]} />
          </div>
        )}
      </QueryState>
      {sub === "edit" && e && <EmployeeForm initial={e} onClose={() => setSub(null)} />}
      {sub === "assign" && e && <AssignmentForm e={e} onClose={() => setSub(null)} />}
      {typeof sub === "object" && sub && "end" in sub && e && <EndAssignment e={e} aid={sub.end} onClose={() => setSub(null)} />}
      {sub === "archive" && <ConfirmModal title="Отправить сотрудника в архив?" danger confirmLabel="В архив" text="Из архива можно вернуть. Если у сотрудника есть незакрытые путевые листы или закреплённая техника, сначала закройте их." onConfirm={() => archive.mutateAsync()} onClose={() => setSub(null)} />}
    </Modal>
  );
}

export function Employees() {
  const { can } = useAuth();
  const entity = useEntityParam();
  const [s, setS] = useUrlState({ q: "", kind: "", archived: "", page: "1" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);

  const params = { q: s.q, is_driver: s.kind === "drivers" ? true : s.kind === "others" ? "false" : undefined, archived: s.archived === "1", legal_entity_id: entity, page: Number(s.page), page_size: 25 };
  const list = useQuery({ queryKey: ["employees", params], queryFn: () => get<Page<Employee>>("/employees", params), placeholderData: (p) => p });
  const multi = entity === undefined;

  return (
    <>
      <PageHeader title="Сотрудники" sub={list.data ? `Всего: ${list.data.total}` : undefined}
        actions={can("employees.create") && <Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>Добавить сотрудника</Button>} />
      <Card flush>
        <div className="toolbar" style={{ padding: "14px 14px 0" }}>
          <div style={{ position: "relative", minWidth: 240, flex: "1 1 240px", maxWidth: 360 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Фамилия, имя, должность" aria-label="Поиск по сотрудникам" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <label className="check"><input type="checkbox" checked={s.archived === "1"} onChange={(e) => setS({ archived: e.target.checked ? "1" : "" })} /> Архив</label>
        </div>
        <div style={{ padding: "12px 14px" }}>
          <FilterChips value={s.kind} onChange={(v) => setS({ kind: v })} options={[{ id: "", label: "Все" }, { id: "drivers", label: "Водители" }, { id: "others", label: "Остальные" }]} />
        </div>
        <QueryState q={list}>
          {list.data && (list.data.items.length === 0 ? (
            <Empty icon={UsersIcon} title={s.q || s.kind ? "Ничего не найдено" : "Сотрудников пока нет"}>{!s.q && !s.kind && can("employees.create") ? "Добавьте первого сотрудника кнопкой выше." : undefined}</Empty>
          ) : (
            <>
              <Table rows={list.data.items} rowKey={(e) => e.id} onRowClick={(e) => setOpen(e.id)} dim={(e) => e.archived}
                columns={[
                  { key: "n", header: "Сотрудник", render: (e) => <strong>{e.full_name}</strong> },
                  { key: "p", header: "Должность", hideSm: true, render: (e) => e.position || "—" },
                  { key: "d", header: "", render: (e) => (e.is_driver ? <Badge tone="info">Водитель</Badge> : null) },
                  ...(multi ? [{ key: "e", header: "Юрлицо", hideSm: true, render: (e: Employee) => e.legal_entities.map((x) => x.name).join(", ") || "—" }] : []),
                  { key: "ph", header: "Телефон", hideSm: true, render: (e) => (e.sensitive_hidden ? <span className="muted">скрыт</span> : e.phone || "—") },
                  { key: "u", header: "Вход в систему", hideSm: true, render: (e) => (e.user_id ? <Badge tone="mint">есть</Badge> : <span className="muted">нет</span>) },
                ]} />
              <Pagination page={list.data.page} pageSize={list.data.page_size} total={list.data.total} onPage={(p) => setS({ page: String(p) })} />
            </>
          ))}
        </QueryState>
      </Card>
      {creating && <EmployeeForm onClose={() => setCreating(false)} />}
      {open !== null && <EmployeeDialog id={open} onClose={() => setOpen(null)} />}
    </>
  );
}
