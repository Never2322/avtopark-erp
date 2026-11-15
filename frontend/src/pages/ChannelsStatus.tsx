import { Mail, Send, ShieldCheck } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api";
import { fmtDateTime } from "../format";
import { Alert, Badge, Card, PageHeader, QueryState, Table } from "../ui";

interface Status {
  telegram: { enabled: boolean; bot: string; linked_users: number };
  email: { enabled: boolean; sender: string; users: number; security: string };
  public_url: string;
  offsite: { enabled: boolean; telegram: boolean; telegram_chats: number; email: boolean; email_to: number; email_max_mb: number };
  counts: Record<string, Record<string, number>>;
  failed: { id: number; channel: string; title: string; error: string; at: string }[];
}

const CHANNEL: Record<string, string> = { telegram: "Telegram", email: "Почта" };
const STATE: [string, string][] = [["sent", "доставлено"], ["new", "ждёт"], ["failed", "не доставлено"], ["skipped", "пропущено"]];

function Counts({ c }: { c?: Record<string, number> }) {
  if (!c) return <span className="muted">за 7 дней сообщений не было</span>;
  return <span className="muted">за 7 дней: {STATE.filter(([k]) => c[k]).map(([k, label]) => `${label} ${c[k]}`).join(", ")}</span>;
}

export function ChannelsStatus() {
  const q = useQuery({ queryKey: ["channels-status"], queryFn: () => get<Status>("/channels/status"), refetchInterval: 15000 });
  const d = q.data;
  return (
    <>
      <PageHeader title="Telegram и почта" sub="Уведомления вне сайта. Включаются ключами в файле настроек сервера; пока ключей нет, всё работает только на сайте" />
      <QueryState q={q}>
        {d && (
          <div className="stack">
            {!d.telegram.enabled && !d.email.enabled && <Alert tone="info">Ни Telegram, ни почта не подключены. Что нужно добавить в файл <span className="mono">.env</span>, описано в документации («Telegram-и-почта.md»).</Alert>}
            {(d.telegram.enabled || d.email.enabled) && !d.public_url && <Alert tone="warning">Не задан адрес сайта (<span className="mono">PUBLIC_URL</span>): сообщения придут без ссылки на страницу.</Alert>}
            <div className="grid cols-2">
              <Card title="Telegram">
                <div className="stack-sm">
                  <div className="row wrap">{d.telegram.enabled ? <Badge tone="success" icon={Send}>Включён</Badge> : <Badge tone="neutral">Выключен (нет ключа бота)</Badge>}{d.telegram.bot && <span className="mono">@{d.telegram.bot}</span>}</div>
                  <div>Подключили Telegram: {d.telegram.linked_users}</div>
                  <Counts c={d.counts.telegram} />
                </div>
              </Card>
              <Card title="Почта">
                <div className="stack-sm">
                  <div className="row wrap">{d.email.enabled ? <Badge tone="success" icon={Mail}>Включена</Badge> : <Badge tone="neutral">Выключена (нет сервера почты)</Badge>}{d.email.sender && <span className="mono">{d.email.sender}</span>}</div>
                  <div>Включили почту: {d.email.users}</div>
                  <Counts c={d.counts.email} />
                </div>
              </Card>
            </div>
            <Card title="Копия вне сервера">
              <div className="stack-sm">
                {d.offsite.enabled ? <div><Badge tone="success" icon={ShieldCheck}>Включена, файл шифруется</Badge></div> : <div><Badge tone="neutral">Выключена: не задан пароль шифрования <span className="mono">OFFSITE_PASSWORD</span></Badge></div>}
                <div>Telegram: {d.offsite.telegram ? `получателей ${d.offsite.telegram_chats}` : "некому отправлять (подключите Telegram у тех, кто видит копии)"}</div>
                <div>Почта: {d.offsite.email ? `адресов ${d.offsite.email_to}, не больше ${d.offsite.email_max_mb} МБ` : "адреса не заданы (OFFSITE_EMAIL_TO)"}</div>
              </div>
            </Card>
            <Card title="Не доставлено" flush>
              {d.failed.length === 0 ? <div className="muted" style={{ padding: 16 }}>Недоставленных сообщений нет.</div> : (
                <Table rows={d.failed} rowKey={(r) => r.id} columns={[
                  { key: "t", header: "Когда", render: (r) => <span className="nowrap">{fmtDateTime(r.at)}</span> },
                  { key: "c", header: "Куда", render: (r) => CHANNEL[r.channel] ?? r.channel },
                  { key: "m", header: "Сообщение", render: (r) => r.title },
                  { key: "e", header: "Причина", render: (r) => <span className="muted">{r.error}</span> },
                ]} />
              )}
            </Card>
          </div>
        )}
      </QueryState>
    </>
  );
}
