import { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { KeyRound, LogOut, ShieldCheck } from "lucide-react";
import { useAuth } from "../auth";
import { ApiError, post } from "../api";
import { Alert, Button, Card, Field, PageHeader, useForm, useToast } from "../ui";
import { ChannelsCard } from "../components/ChannelsCard";

function PasswordForm({ forced }: { forced?: boolean }) {
  const { refresh } = useAuth();
  const toast = useToast();
  const f = useForm({ current_password: "", new_password: "", repeat: "" });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    f.clear();
    if (f.values.new_password !== f.values.repeat) return f.fail(new ApiError(422, "validation", "Новый пароль и повтор не совпадают", "repeat", { repeat: "Пароли не совпадают" }));
    try {
      await post("/auth/password", { current_password: f.values.current_password, new_password: f.values.new_password });
      toast.success("Пароль изменён");
      f.setValues({ current_password: "", new_password: "", repeat: "" });
      await refresh();
    } catch (err) {
      f.fail(err);
    }
  };
  return (
    <form className="stack" onSubmit={submit} style={{ maxWidth: 420 }}>
      {forced && <Alert tone="warning">Вам выдан временный пароль. Придумайте свой, чтобы продолжить работу.</Alert>}
      {f.formError && <Alert tone="error">{f.formError}</Alert>}
      <Field label={forced ? "Временный пароль" : "Текущий пароль"} error={f.errors.current_password}>
        <input className="input" type="password" autoComplete="current-password" value={f.values.current_password} onChange={(e) => f.set("current_password", e.target.value)} required />
      </Field>
      <Field label="Новый пароль" error={f.errors.new_password} hint="Не короче 8 символов, не совпадает с логином и не из простых вроде 12345678">
        <input className="input" type="password" autoComplete="new-password" value={f.values.new_password} onChange={(e) => f.set("new_password", e.target.value)} required />
      </Field>
      <Field label="Повторите новый пароль" error={f.errors.repeat}>
        <input className="input" type="password" autoComplete="new-password" value={f.values.repeat} onChange={(e) => f.set("repeat", e.target.value)} required />
      </Field>
      <div><Button variant="primary" type="submit" icon={KeyRound} disabled={!f.values.current_password || !f.values.new_password}>Сменить пароль</Button></div>
    </form>
  );
}

export function ForcedPassword() {
  const { me, logout } = useAuth();
  const nav = useNavigate();
  return (
    <div className="login-wrap">
      <div className="card login-card stack" style={{ maxWidth: 460 }}>
        <h1>Смена пароля</h1>
        <div className="muted">Здравствуйте, {me!.display_name || me!.login}.</div>
        <PasswordForm forced />
        <Button variant="ghost" icon={LogOut} onClick={async () => { await logout(); nav("/login"); }}>Выйти</Button>
      </div>
    </div>
  );
}

export function Profile() {
  const { me, logout } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const others = async () => {
    const r = await post<{ ended: number }>("/auth/logout-others");
    toast.success(r.ended ? `Завершено других входов: ${r.ended}` : "Других входов нет");
  };
  return (
    <>
      <PageHeader title="Профиль" sub={me!.display_name || me!.login} />
      <div className="grid cols-2">
        <Card title="Пароль"><PasswordForm /></Card>
        <div className="stack">
          <Card title="Доступ">
            <dl className="kv">
              <dt>Логин</dt><dd>{me!.login}</dd>
              <dt>Карточка сотрудника</dt><dd>{me!.employee_name ?? <span className="muted">не привязана</span>}</dd>
              <dt>Роли</dt>
              <dd>{me!.roles.length ? me!.roles.map((r, i) => (
                <div key={i}>{r.name} <span className="muted">— {r.legal_entity_id ? me!.legal_entities.find((e) => e.id === r.legal_entity_id)?.short_name ?? "юрлицо" : "все юрлица"}</span></div>
              )) : <span className="muted">нет</span>}</dd>
            </dl>
          </Card>
          <ChannelsCard />
          <Card title="Входы">
            <p className="muted" style={{ marginBottom: 12 }}>Если входили с чужого или потерянного устройства, завершите все остальные входы.</p>
            <div className="row wrap">
              <Button icon={ShieldCheck} onClick={others}>Выйти на других устройствах</Button>
              <Button variant="danger" icon={LogOut} onClick={async () => { await logout(); nav("/login"); }}>Выйти здесь</Button>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
