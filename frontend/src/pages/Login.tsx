import { FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { LogIn, Truck } from "lucide-react";
import { useAuth } from "../auth";
import { Alert, Button, Field } from "../ui";
import { errorText } from "../api";

export function Login() {
  const { login, sessionExpired } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(name.trim(), password, remember);
      nav((loc.state as { from?: string } | null)?.from || "/", { replace: true });
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="card login-card stack" onSubmit={submit} noValidate>
        <div className="row" style={{ gap: 12 }}>
          <span className="brand" style={{ padding: 0 }}><span className="logo"><Truck size={18} aria-hidden /></span></span>
          <div>
            <h1>Вход в систему</h1>
            <div className="muted small">Автопарк: учёт техники и ремонтов</div>
          </div>
        </div>
        {sessionExpired && !error && <Alert tone="warning">Сессия закончилась. Войдите снова.</Alert>}
        {error && <Alert tone="error">{error}</Alert>}
        <Field label="Логин">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="username" autoCapitalize="none" autoFocus required />
        </Field>
        <Field label="Пароль">
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </Field>
        <label className="check"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Запомнить на этом устройстве (30 дней)</label>
        <Button variant="primary" type="submit" icon={LogIn} loading={busy} disabled={!name.trim() || !password}>Войти</Button>
        <div className="muted small">Забыли пароль? Попросите администратора выдать новый временный пароль.</div>
      </form>
    </div>
  );
}
