import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Building2, Pencil, Plus, RotateCcw } from "lucide-react";
import { get, patch, post } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, FilterChips, Modal, PageHeader, QueryState, Table, useForm, useToast } from "../ui";

interface Entity { id: number; name: string; short_name: string; inn: string; kpp: string; ogrn: string; address: string; phone: string; email: string; bank_name: string; bik: string; account: string; corr_account: string; head_name: string; accountant_name: string; archived: boolean; vehicles: number; employees: number }

function EntityForm({ initial, onClose }: { initial?: Entity; onClose: () => void }) {
  const qc = useQueryClient();
  const { refresh } = useAuth();
  const toast = useToast();
  const f = useForm({ name: initial?.name ?? "", short_name: initial?.short_name ?? "", inn: initial?.inn ?? "", kpp: initial?.kpp ?? "", ogrn: initial?.ogrn ?? "", address: initial?.address ?? "", phone: initial?.phone ?? "", email: initial?.email ?? "",
    bank_name: initial?.bank_name ?? "", bik: initial?.bik ?? "", account: initial?.account ?? "", corr_account: initial?.corr_account ?? "", head_name: initial?.head_name ?? "", accountant_name: initial?.accountant_name ?? "" });
  const m = useMutation({
    mutationFn: () => (initial ? patch(`/legal-entities/${initial.id}`, f.values) : post("/legal-entities", f.values)),
    onSuccess: async () => { qc.invalidateQueries(); await refresh(); toast.success(initial ? "Сохранено" : "Юрлицо добавлено"); onClose(); },
    onError: f.fail,
  });
  const set = (k: keyof typeof f.values) => (e: { target: { value: string } }) => f.set(k, e.target.value);
  return (
    <Modal title={initial ? "Изменить юрлицо" : "Добавить юрлицо"} wide onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={f.values.name.trim().length < 2} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button></>}>
      <div className="stack">
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          <Field label="Полное название" full error={f.errors.name}><input className="input" value={f.values.name} maxLength={200} onChange={set("name")} /></Field>
          <Field label="Короткое название" error={f.errors.short_name} hint="Показывается в переключателе вверху"><input className="input" value={f.values.short_name} maxLength={80} onChange={set("short_name")} /></Field>
          <Field label="ИНН" error={f.errors.inn} hint="10 цифр у организации, 12 у предпринимателя"><input className="input" inputMode="numeric" value={f.values.inn} maxLength={12} onChange={set("inn")} /></Field>
          <Field label="КПП" error={f.errors.kpp}><input className="input" inputMode="numeric" value={f.values.kpp} maxLength={9} onChange={set("kpp")} /></Field>
          <Field label="ОГРН" error={f.errors.ogrn}><input className="input" inputMode="numeric" value={f.values.ogrn} maxLength={15} onChange={set("ogrn")} /></Field>
          <Field label="Адрес" full error={f.errors.address}><input className="input" value={f.values.address} maxLength={300} onChange={set("address")} /></Field>
          <Field label="Телефон" error={f.errors.phone}><input className="input" inputMode="tel" value={f.values.phone} maxLength={40} onChange={set("phone")} /></Field>
          <Field label="Почта" error={f.errors.email}><input className="input" type="email" value={f.values.email} maxLength={120} onChange={set("email")} /></Field>
        </div>
        <h3 className="form-section">Реквизиты для счетов и актов</h3>
        <div className="form-grid">
          <Field label="Банк" full error={f.errors.bank_name}><input className="input" value={f.values.bank_name} maxLength={120} onChange={set("bank_name")} /></Field>
          <Field label="БИК" error={f.errors.bik} hint="9 цифр"><input className="input" inputMode="numeric" value={f.values.bik} maxLength={9} onChange={set("bik")} /></Field>
          <Field label="Расчётный счёт" error={f.errors.account} hint="20 цифр"><input className="input" inputMode="numeric" value={f.values.account} maxLength={20} onChange={set("account")} /></Field>
          <Field label="Корр. счёт" error={f.errors.corr_account} hint="20 цифр"><input className="input" inputMode="numeric" value={f.values.corr_account} maxLength={20} onChange={set("corr_account")} /></Field>
          <Field label="Руководитель" error={f.errors.head_name} hint="Подпись на документах"><input className="input" value={f.values.head_name} maxLength={120} onChange={set("head_name")} /></Field>
          <Field label="Главный бухгалтер" error={f.errors.accountant_name}><input className="input" value={f.values.accountant_name} maxLength={120} onChange={set("accountant_name")} /></Field>
        </div>
      </div>
    </Modal>
  );
}

function Departments({ entity, canManage }: { entity: Entity; canManage: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const q = useQuery({ queryKey: ["departments", entity.id], queryFn: () => get<{ items: { id: number; name: string }[] }>(`/legal-entities/${entity.id}/departments`) });
  const add = useMutation({
    mutationFn: () => post(`/legal-entities/${entity.id}/departments`, { name }),
    onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["departments", entity.id] }); toast.success("Подразделение добавлено"); },
    onError: (e: any) => toast.error(e.message),
  });
  return (
    <div className="stack-sm">
      <QueryState q={q}>{q.data?.items.length ? <div className="row wrap">{q.data.items.map((d) => <Badge key={d.id}>{d.name}</Badge>)}</div> : <span className="muted">Подразделений пока нет</span>}</QueryState>
      {canManage && !entity.archived && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim().length >= 2) add.mutate(); }}>
          <input className="input compact" aria-label="Название подразделения" placeholder="Новое подразделение" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
          <Button type="submit" size="sm" icon={Plus} loading={add.isPending} disabled={name.trim().length < 2}>Добавить</Button>
        </form>
      )}
    </div>
  );
}

export function Entities() {
  const { can, refresh } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [archived, setArchived] = useState("");
  const [editing, setEditing] = useState<Entity | "new" | null>(null);
  const [deps, setDeps] = useState<Entity | null>(null);
  const [archiving, setArchiving] = useState<Entity | null>(null);
  const manage = can("legal_entities.manage");
  const q = useQuery({ queryKey: ["entities", archived], queryFn: () => get<{ items: Entity[] }>("/legal-entities", { archived: archived === "1" }) });
  const restore = useMutation({
    mutationFn: (id: number) => post(`/legal-entities/${id}/restore`),
    onSuccess: async () => { qc.invalidateQueries(); await refresh(); toast.success("Юрлицо возвращено"); },
    onError: (e: any) => toast.error(e.message),
  });
  const archive = useMutation({ mutationFn: (id: number) => post(`/legal-entities/${id}/archive`), onSuccess: async () => { qc.invalidateQueries(); await refresh(); } });

  return (
    <>
      <PageHeader title="Юридические лица" sub="От юрлица зависит, кто какие данные видит" actions={manage && <Button variant="primary" icon={Plus} onClick={() => setEditing("new")}>Добавить юрлицо</Button>} />
      <Card flush>
        <div style={{ padding: "14px" }}><FilterChips value={archived} onChange={setArchived} options={[{ id: "", label: "Действующие" }, { id: "1", label: "Архив" }]} /></div>
        <QueryState q={q}>
          {q.data && (q.data.items.length === 0 ? <Empty icon={Building2} title={archived ? "В архиве пусто" : "Юрлиц пока нет"} /> : (
            <Table rows={q.data.items} rowKey={(e) => e.id} dim={(e) => e.archived}
              columns={[
                { key: "n", header: "Название", render: (e) => <><strong>{e.short_name || e.name}</strong>{e.short_name && <div className="muted small">{e.name}</div>}</> },
                { key: "inn", header: "ИНН", hideSm: true, render: (e) => e.inn || "—" },
                { key: "v", header: "Техника", num: true, render: (e) => e.vehicles },
                { key: "emp", header: "Сотрудников", num: true, hideSm: true, render: (e) => e.employees },
                { key: "dep", header: "", render: (e) => <Button size="sm" onClick={() => setDeps(e)}>Подразделения</Button> },
                { key: "act", header: "", render: (e) => manage ? (
                  <div className="row">
                    {!e.archived && <Button size="sm" icon={Pencil} onClick={() => setEditing(e)}>Изменить</Button>}
                    {e.archived ? <Button size="sm" icon={RotateCcw} onClick={() => restore.mutate(e.id)}>Вернуть</Button> : <Button size="sm" variant="danger" icon={Archive} onClick={() => setArchiving(e)}>В архив</Button>}
                  </div>
                ) : null },
              ]} />
          ))}
        </QueryState>
      </Card>
      {editing && <EntityForm initial={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
      {deps && <Modal title={`Подразделения: ${deps.short_name || deps.name}`} onClose={() => setDeps(null)}><Departments entity={deps} canManage={manage} /></Modal>}
      {archiving && <ConfirmModal title="Отправить юрлицо в архив?" danger confirmLabel="В архив" text="Если в юрлице есть действующая техника, сначала отправьте её в архив. Из архива юрлицо можно вернуть." onConfirm={() => archive.mutateAsync(archiving.id)} onClose={() => setArchiving(null)} />}
    </>
  );
}
