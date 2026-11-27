import { FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Eye, EyeOff, Lock, LogIn, ShieldCheck, User } from "lucide-react";
import { useAuth } from "../auth";
import { Alert, Button } from "../ui";
import { errorText } from "../api";
import { ThemeToggle } from "../components/ThemeToggle";

export function Login() {
  const { login, sessionExpired } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [show, setShow] = useState(false);
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
      {/* картинка «Контур» целиком: логотип уже на ней, форма встаёт ровно на нарисованную */}
      <div className="login-theme"><ThemeToggle /></div>
      <div className="login-stage">
        <h2 className="sr-only">Контур</h2>
        <form className="card login-card stack" onSubmit={submit} noValidate>
          <div className="login-head">
            <span className="login-badge" aria-hidden>
              <svg viewBox="0 0 40 40"><path className="k-blue" d="M7 16 V7 H17" /><path className="k-red" d="M23 7 H33 V16" /><path className="k-red" d="M7 24 V33 H17" /><path className="k-blue" d="M23 33 H33 V24" /></svg>
            </span>
            <div>
              <h1>Вход в систему</h1>
              <div className="muted small">Авторизуйтесь, чтобы продолжить работу</div>
            </div>
          </div>
          {sessionExpired && !error && <Alert tone="warning">Сессия закончилась. Войдите снова.</Alert>}
          {error && <Alert tone="error">{error}</Alert>}
          <label className="login-field">
            <span className="login-label"><User size={16} aria-hidden /> Логин</span>
            <span className="login-input">
              <input className="input" placeholder="Введите логин" value={name} onChange={(e) => setName(e.target.value)} autoComplete="username" autoCapitalize="none" autoFocus required />
              <User size={18} aria-hidden className="login-input-ico" />
            </span>
          </label>
          <label className="login-field">
            <span className="login-label"><Lock size={16} aria-hidden /> Пароль</span>
            <span className="login-input">
              <input className="input" type={show ? "text" : "password"} placeholder="Введите пароль" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
              <button type="button" className="login-eye" onClick={() => setShow((v) => !v)} aria-label={show ? "Скрыть пароль" : "Показать пароль"} title={show ? "Скрыть пароль" : "Показать пароль"}>
                {show ? <Eye size={18} aria-hidden /> : <EyeOff size={18} aria-hidden />}
              </button>
            </span>
          </label>
          <label className="check"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Запомнить на этом устройстве (30 дней)</label>
          <Button variant="primary" className="login-go" type="submit" icon={LogIn} loading={busy} disabled={!name.trim() || !password}>Войти</Button>
          <div className="login-foot muted small"><ShieldCheck size={22} aria-hidden /> <span>Забыли пароль? Обратитесь к администратору для восстановления доступа.</span></div>
        </form>
      </div>
    </div>
  );
}
