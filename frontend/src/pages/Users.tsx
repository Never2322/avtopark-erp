import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, LogOut, Pencil, Plus, Search, Trash2, UserCog } from "lucide-react";
import { get, patch, post } from "../api";
import { useAuth } from "../auth";
import { fmtDateTime } from "../format";
import { Page, useDebounced, useUrlState } from "../hooks";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, FilterChips, Modal, PageHeader, Pagination, QueryState, Table, useForm, useToast } from "../ui";

interface UserRow {
  id: number; login: string; display_name: string; is_active: boolean; must_change_password: boolean; last_login_at: string | null; last_login_ip: string | null;
  employee_id: number | null; employee_name: string | null; roles: { role_id: number; code: string; name: string; legal_entity_id: number | null; legal_entity: string }[];
}
interface Role { id: number; code: string; name: string; description: string }
interface Grant { role_id: string; legal_entity_id: string }

function PasswordBox({ login, password, onClose }: { login: string; password: string; onClose: () => void }) {
  const toast = useToast();
  const copy = async () => {
    try { await navigator.clipboard.writeText(password); toast.success("Пароль скопирован"); } catch { toast.error("Не удалось скопировать: выделите пароль и скопируйте вручную"); }
  };
  return (
    <Modal title="Временный пароль" onClose={onClose} footer={<><Button icon={Copy} onClick={copy}>Скопировать</Button><Button variant="primary" onClick={onClose}>Готово</Button></>}>
      <div className="stack">
        <div>Логин: <strong>{login}</strong></div>
        <div className="pw-box" aria-label="Временный пароль">{password}</div>
        <Alert tone="warning">Пароль показывается один раз: в базе остаётся только его защищённый след. Передайте его сотруднику. При первом входе система попросит придумать свой пароль.</Alert>
      </div>
    </Modal>
  );
}

function UserForm({ initial, onClose, onCreated }: { initial?: UserRow; onClose: () => void; onCreated: (login: string, password: string) => void }) {
  const { me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => get<{ items: Role[] }>("/roles") });
  const emps = useQuery({ queryKey: ["employee-options"], queryFn: () => get<{ items: { id: number; full_name: string }[] }>("/employees/lookup") });
  const f = useForm({
    login: initial?.login ?? "", display_name: initial?.display_name ?? "", password: "", is_active: initial?.is_active ?? true,
    employee_id: initial?.employee_id ? String(initial.employee_id) : "",
  });
  const [grants, setGrants] = useState<Grant[]>(initial ? initial.roles.map((r) => ({ role_id: String(r.role_id), legal_entity_id: r.legal_entity_id ? String(r.legal_entity_id) : "" })) : [{ role_id: "", legal_entity_id: "" }]);
  const body = () => ({
    display_name: f.values.display_name, is_active: f.values.is_active, employee_id: f.values.employee_id ? Number(f.values.employee_id) : null,
    roles: grants.filter((g) => g.role_id).map((g) => ({ role_id: Number(g.role_id), legal_entity_id: g.legal_entity_id ? Number(g.legal_entity_id) : null })),
  });
  const m = useMutation({
    mutationFn: () => (initial ? patch<UserRow>(`/users/${initial.id}`, body()) : post<UserRow & { temporary_password: string }>("/users", { ...body(), login: f.values.login, password: f.values.password || null })),
    onSuccess: (r: any) => {
      qc.invalidateQueries();
      if (!initial) { onClose(); onCreated(r.login, r.temporary_password); } else { toast.success("Сохранено"); onClose(); }
    },
    onError: f.fail,
  });
  const setGrant = (i: number, patchG: Partial<Grant>) => setGrants((g) => g.map((x, j) => (j === i ? { ...x, ...patchG } : x)));
  const options = emps.data?.items ?? [];
  const missing = initial?.employee_id && !options.some((o) => o.id === initial.employee_id);
  return (
    <Modal title={initial ? `Пользователь ${initial.login}` : "Новый пользователь"} wide onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" loading={m.isPending} disabled={!initial && f.values.login.trim().length < 3} onClick={() => { f.clear(); m.mutate(); }}>{initial ? "Сохранить" : "Создать"}</Button>
    </>}>
      <div className="stack">
        {f.formError && !f.errors.login && !f.errors.roles && !f.errors.password && !f.errors.employee_id && !f.errors.is_active && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          {!initial && <Field label="Логин" error={f.errors.login} hint="Латиница, цифры, точка, дефис. От 3 до 32 символов"><input className="input" autoCapitalize="none" value={f.values.login} maxLength={32} onChange={(e) => f.set("login", e.target.value)} /></Field>}
          <Field label="Имя для списков" error={f.errors.display_name}><input className="input" value={f.values.display_name} maxLength={120} onChange={(e) => f.set("display_name", e.target.value)} /></Field>
          {!initial && <Field label="Пароль" error={f.errors.password} hint="Оставьте пустым: программа придумает временный пароль"><input className="input" type="text" autoComplete="off" value={f.values.password} onChange={(e) => f.set("password", e.target.value)} /></Field>}
          <Field label="Карточка сотрудника" error={f.errors.employee_id} hint="Нужна водителям: по ней видны их машины и листы">
            <select className="input" value={f.values.employee_id} onChange={(e) => f.set("employee_id", e.target.value)}>
              <option value="">Не привязана</option>
              {missing && <option value={initial!.employee_id!}>{initial!.employee_name}</option>}
              {options.map((o) => <option key={o.id} value={o.id}>{o.full_name}</option>)}
            </select>
          </Field>
          {initial && <Field label="Вход разрешён" error={f.errors.is_active}><label className="check" style={{ minHeight: 40 }}><input type="checkbox" checked={f.values.is_active} onChange={(e) => f.set("is_active", e.target.checked)} /> Пользователь может входить</label></Field>}
        </div>
        <div className="stack-sm">
          <strong>Роли</strong>
          {f.errors.roles && <span className="err" role="alert" style={{ color: "var(--error)" }}>{f.errors.roles}</span>}
          {grants.map((g, i) => (
            <div className="row wrap" key={i}>
              <select className="input compact" aria-label="Роль" value={g.role_id} onChange={(e) => setGrant(i, { role_id: e.target.value })}>
                <option value="">Выберите роль…</option>
                {roles.data?.items.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
              <select className="input compact" aria-label="Где действует" value={g.legal_entity_id} onChange={(e) => setGrant(i, { legal_entity_id: e.target.value })}>
                <option value="">Во всех юрлицах</option>
                {me!.legal_entities.map((e) => <option key={e.id} value={e.id}>{e.short_name || e.name}</option>)}
              </select>
              {grants.length > 1 && <Button size="sm" variant="ghost" icon={Trash2} aria-label="Убрать роль" onClick={() => setGrants((x) => x.filter((_, j) => j !== i))} />}
            </div>
          ))}
          <div><Button size="sm" icon={Plus} onClick={() => setGrants((x) => [...x, { role_id: "", legal_entity_id: "" }])}>Ещё роль</Button></div>
          <div className="muted small">Роль можно ограничить одним юрлицом: тогда человек видит данные только его. Права ролей меняются в разделе «Роли и права».</div>
        </div>
      </div>
    </Modal>
  );
}

export function Users() {
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [s, setS] = useUrlState({ q: "", active: "", page: "1" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  const [form, setForm] = useState<UserRow | "new" | null>(null);
  const [pw, setPw] = useState<{ login: string; password: string } | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "reset" | "logout"; u: UserRow } | null>(null);
  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);
  const manage = can("users.manage");
  const params = { q: s.q, active: s.active === "1" ? true : s.active === "0" ? "false" : undefined, page: Number(s.page), page_size: 25 };
  const list = useQuery({ queryKey: ["users", params], queryFn: () => get<Page<UserRow>>("/users", params), placeholderData: (p) => p });
  const reset = useMutation({ mutationFn: (u: UserRow) => post<{ temporary_password: string }>(`/users/${u.id}/reset-password`), onSuccess: (r, u) => { qc.invalidateQueries(); setPw({ login: u.login, password: r.temporary_password }); } });
  const logout = useMutation({ mutationFn: (u: UserRow) => post<{ ended: number }>(`/users/${u.id}/logout-all`), onSuccess: (r) => toast.success(r.ended ? `Завершено сеансов: ${r.ended}` : "Активных сеансов не было") });

  return (
    <>
      <PageHeader title="Пользователи" sub="Кто может входить в систему и с какими ролями" actions={manage && <Button variant="primary" icon={Plus} onClick={() => setForm("new")}>Новый пользователь</Button>} />
      <Card flush>
        <div className="toolbar" style={{ padding: "14px 14px 0" }}>
          <div style={{ position: "relative", minWidth: 240, flex: "1 1 240px", maxWidth: 360 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Логин или имя" aria-label="Поиск по пользователям" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
        </div>
        <div style={{ padding: "12px 14px" }}><FilterChips value={s.active} onChange={(v) => setS({ active: v })} options={[{ id: "", label: "Все" }, { id: "1", label: "Работают" }, { id: "0", label: "Отключены" }]} /></div>
        <QueryState q={list}>
          {list.data && (list.data.items.length === 0 ? <Empty icon={UserCog} title="Никого не найдено" /> : (
            <>
              <Table rows={list.data.items} rowKey={(u) => u.id} dim={(u) => !u.is_active}
                columns={[
                  { key: "u", header: "Пользователь", render: (u) => <><strong>{u.login}</strong>{u.display_name && <div className="muted small">{u.display_name}</div>}</> },
                  { key: "r", header: "Роли", render: (u) => <div className="row wrap">{u.roles.map((r, i) => <Badge key={i} tone={r.legal_entity_id ? "neutral" : "mint"}>{r.name}{r.legal_entity_id ? ` · ${r.legal_entity}` : ""}</Badge>)}</div> },
                  { key: "e", header: "Сотрудник", hideSm: true, render: (u) => u.employee_name ?? <span className="muted">не привязан</span> },
                  { key: "l", header: "Последний вход", hideSm: true, render: (u) => (u.last_login_at ? <>{fmtDateTime(u.last_login_at)}</> : <span className="muted">ещё не входил</span>) },
                  { key: "s", header: "Статус", render: (u) => (u.is_active ? (u.must_change_password ? <Badge tone="warning">Ждёт смены пароля</Badge> : <Badge tone="success">Работает</Badge>) : <Badge>Отключён</Badge>) },
                  { key: "a", header: "", render: (u) => manage ? (
                    <div className="row">
                      <Button size="sm" icon={Pencil} onClick={() => setForm(u)}>Изменить</Button>
                      <Button size="sm" icon={KeyRound} aria-label={`Сбросить пароль ${u.login}`} onClick={() => setConfirm({ kind: "reset", u })}>Пароль</Button>
                      <Button size="sm" icon={LogOut} aria-label={`Завершить сеансы ${u.login}`} onClick={() => setConfirm({ kind: "logout", u })} />
                    </div>
                  ) : null },
                ]} />
              <Pagination page={list.data.page} pageSize={list.data.page_size} total={list.data.total} onPage={(p) => setS({ page: String(p) })} />
            </>
          ))}
        </QueryState>
      </Card>
      {form && <UserForm initial={form === "new" ? undefined : form} onClose={() => setForm(null)} onCreated={(login, password) => setPw({ login, password })} />}
      {pw && <PasswordBox login={pw.login} password={pw.password} onClose={() => setPw(null)} />}
      {confirm?.kind === "reset" && <ConfirmModal title={`Сбросить пароль ${confirm.u.login}?`} confirmLabel="Сбросить" danger text="Старый пароль перестанет работать, все сеансы пользователя завершатся. Новый временный пароль покажем один раз." onConfirm={() => reset.mutateAsync(confirm.u)} onClose={() => setConfirm(null)} />}
      {confirm?.kind === "logout" && <ConfirmModal title={`Завершить все сеансы ${confirm.u.login}?`} confirmLabel="Завершить" text="Пользователя выбросит из системы на всех устройствах. Войти снова он сможет со своим паролем." onConfirm={() => logout.mutateAsync(confirm.u)} onClose={() => setConfirm(null)} />}
    </>
  );
}
