import { useEffect, useRef, useState } from "react";
import { ExternalLink, Mail, Send, Unlink } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorText, get, post, put } from "../api";
import { Alert, Badge, Button, Card, Field, useToast } from "../ui";

interface State {
  telegram: { available: boolean; bot: string; linked: boolean; name: string; on: boolean; code: string | null; code_expires_at: string | null; link: string };
  email: { available: boolean; address: string; on: boolean };
}
interface TestResult { result: Record<string, { ok: boolean; error?: string }> }

/** «Уведомления вне сайта»: Telegram привязывается кодом, который человек присылает боту; почта — просто адрес. */
export function ChannelsCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const [address, setAddress] = useState<string | null>(null);
  const [emailError, setEmailError] = useState("");
  const wasLinked = useRef<boolean | null>(null);
  const q = useQuery({
    queryKey: ["me-channels"], queryFn: () => get<State>("/me/channels"),
    refetchInterval: (query) => (query.state.data?.telegram.code ? 3000 : false),          // пока ждём, что человек напишет боту
  });
  const d = q.data;
  useEffect(() => {
    if (!d) return;
    if (wasLinked.current === false && d.telegram.linked) toast.success("Telegram подключён");
    wasLinked.current = d.telegram.linked;
  }, [d?.telegram.linked]);                                                                  // eslint-disable-line react-hooks/exhaustive-deps

  const set = (s: State) => qc.setQueryData(["me-channels"], s);
  const code = useMutation({ mutationFn: () => post<State>("/me/channels/telegram/code"), onSuccess: set, onError: (e) => toast.error(errorText(e)) });
  const unlink = useMutation({ mutationFn: () => api<State>("DELETE", "/me/channels/telegram"), onSuccess: (s) => { set(s); toast.success("Telegram отключён"); }, onError: (e) => toast.error(errorText(e)) });
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => put<State>("/me/channels", body),
    onSuccess: (s) => { set(s); setAddress(null); setEmailError(""); },
    onError: (e: any) => { if (e?.field === "email") setEmailError(e.message); else toast.error(errorText(e)); },
  });
  const test = useMutation({
    mutationFn: () => post<TestResult>("/me/channels/test"),
    onSuccess: ({ result }) => {
      const bad = Object.entries(result).filter(([, v]) => !v.ok);
      if (!Object.keys(result).length) toast.error("Нечего проверять: ничего не подключено");
      else if (bad.length) toast.error(bad.map(([k, v]) => `${k === "telegram" ? "Telegram" : "Почта"}: ${v.error}`).join(". "));
      else toast.success("Пробное сообщение отправлено: проверьте Telegram и почту");
    },
    onError: (e) => toast.error(errorText(e)),
  });

  if (!d) return <Card title="Уведомления вне сайта"><div className="muted">Загрузка…</div></Card>;
  if (!d.telegram.available && !d.email.available) {
    return (
      <Card title="Уведомления вне сайта">
        <Alert tone="info">Telegram и почта на сервере пока не подключены. Уведомления приходят на сайте (колокольчик). Подключить их может администратор: нужны ключ бота и данные почтового сервера (описано в документации).</Alert>
      </Card>
    );
  }
  const typed = address ?? d.email.address;
  const anything = d.telegram.linked || d.email.on;
  return (
    <Card title="Уведомления вне сайта">
      <p className="muted" style={{ marginBottom: 12 }}>Те же уведомления, что в колокольчике, можно получать в Telegram и на почту.</p>
      <div className="stack">
        {d.telegram.available && (
          <div className="stack-sm">
            <div className="row wrap" style={{ gap: 8 }}><strong>Telegram</strong>{d.telegram.linked ? <Badge tone={d.telegram.on ? "success" : "neutral"}>{d.telegram.on ? "Подключён" : "Выключен"}</Badge> : <Badge tone="neutral">Не подключён</Badge>}</div>
            {d.telegram.linked ? (
              <div className="row wrap">
                <span className="muted">{d.telegram.name || "Ваш аккаунт"}</span>
                <label className="check"><input type="checkbox" checked={d.telegram.on} onChange={(e) => save.mutate({ telegram_on: e.target.checked })} /> Присылать</label>
                <Button size="sm" icon={Unlink} loading={unlink.isPending} onClick={() => unlink.mutate()}>Отключить</Button>
              </div>
            ) : d.telegram.code ? (
              <div className="stack-sm">
                <div>Откройте бота{d.telegram.bot ? <> <strong>@{d.telegram.bot}</strong></> : ""} в Telegram и отправьте ему этот код (действует 15 минут):</div>
                <div className="mono" style={{ fontSize: 26, letterSpacing: 4, fontWeight: 600 }} aria-label="Код привязки">{d.telegram.code}</div>
                <div className="row wrap">
                  {d.telegram.link && <a className="btn sm" href={d.telegram.link} target="_blank" rel="noreferrer"><ExternalLink size={14} aria-hidden /> Открыть бота</a>}
                  <span className="muted small">Страница сама заметит подключение.</span>
                </div>
              </div>
            ) : (
              <div><Button size="sm" icon={Send} loading={code.isPending} onClick={() => code.mutate()}>Подключить Telegram</Button></div>
            )}
          </div>
        )}
        {d.email.available && (
          <div className="stack-sm">
            <div className="row wrap" style={{ gap: 8 }}><strong>Почта</strong>{d.email.on ? <Badge tone="success">Включена</Badge> : <Badge tone="neutral">Выключена</Badge>}</div>
            <Field label="Адрес почты" error={emailError}>
              <input className="input" type="email" inputMode="email" autoComplete="email" placeholder="name@example.ru" value={typed} onChange={(e) => { setAddress(e.target.value); setEmailError(""); }} />
            </Field>
            <div className="row wrap">
              <label className="check"><input type="checkbox" checked={d.email.on} disabled={!d.email.address} onChange={(e) => save.mutate({ email_on: e.target.checked })} /> Присылать на почту</label>
              <Button size="sm" icon={Mail} loading={save.isPending} disabled={address === null || address === d.email.address} onClick={() => save.mutate({ email: typed, email_on: typed.trim() ? true : false })}>Сохранить адрес</Button>
            </div>
          </div>
        )}
        <div><Button size="sm" loading={test.isPending} disabled={!anything} onClick={() => test.mutate()}>Прислать пробное сообщение</Button></div>
      </div>
    </Card>
  );
}
