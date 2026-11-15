import { Download, Database, Loader2, Play, Send, ShieldCheck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { fmtDateTime, fmtNum, fmtSize } from "../format";
import { Alert, Badge, Button, Card, Empty, PageHeader, QueryState, Table, useToast } from "../ui";

interface Check { state: string; ok?: boolean; mismatches?: { table: string; before: number; after: number }[]; checked_tables?: number; schema_ok?: boolean; protective_triggers?: number; rows_total?: number; seconds?: number; error?: string }
interface Job {
  id: number; status: string; trigger_label: string; started_at: string; finished_at: string | null; filename: string | null; size_bytes: number | null; error: string | null;
  file_available: boolean; file_deleted: boolean; initiated_by: string | null; tables: number; rows_total: number; restore_checked_at: string | null;
  restore_check: Check | null; verified: boolean; verifying: boolean; offsite: Offsite | null;
}
interface Offsite { encrypted?: boolean; at?: string; telegram?: { ok: boolean; sent?: number; error?: string }; email?: { ok: boolean; sent?: number; error?: string } }
interface Plan { enabled: boolean; telegram: boolean; telegram_chats: number; email: boolean; email_to: number; email_max_mb: number }
interface Data { items: Job[]; warnings: string[]; settings: { keep: number; schedule: string; hour: number; timezone: string; offsite: Plan } }

function Away({ j }: { j: Job }) {
  const o = j.offsite;
  if (j.status !== "ok") return <span className="muted">—</span>;
  if (!o || (!o.telegram && !o.email)) return <span className="muted small">не отправлялась</span>;
  const one = (name: string, r?: { ok: boolean; sent?: number; error?: string }) => r && (
    <div>{r.ok ? <Badge tone="success">{name}: отправлено</Badge> : <Badge tone="error">{name}: не вышло</Badge>}{!r.ok && r.error && <div className="muted small" style={{ maxWidth: 260 }}>{r.error}</div>}</div>
  );
  return <div className="stack-sm">{one("Telegram", o.telegram)}{one("Почта", o.email)}{o.at && <div className="muted small">{fmtDateTime(o.at)}</div>}</div>;
}

function Verdict({ j }: { j: Job }) {
  if (j.status !== "ok") return <span className="muted">—</span>;
  if (j.verifying) return <Badge tone="info" icon={Loader2}>Проверяется…</Badge>;
  const c = j.restore_check;
  if (!c) return <Badge tone="warning">Не проверена</Badge>;
  if (c.ok) return <Badge tone="success" icon={ShieldCheck}>Восстановлена и сверена</Badge>;
  return <Badge tone="error">Проверка не прошла</Badge>;
}

export function Backups() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({
    queryKey: ["backups"], queryFn: () => get<Data>("/backups"),
    refetchInterval: (query) => (query.state.data?.items.some((j) => j.status === "running" || j.verifying) ? 2500 : false),
  });
  const start = useMutation({ mutationFn: () => post("/backups"), onSuccess: () => { qc.invalidateQueries({ queryKey: ["backups"] }); toast.success("Копия запущена: это займёт до пары минут"); }, onError: (e: any) => toast.error(e.message) });
  const verify = useMutation({ mutationFn: (id: number) => post(`/backups/${id}/verify`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["backups"] }); toast.success("Проверка запущена: копию разворачивают во временную базу"); }, onError: (e: any) => toast.error(e.message) });
  const away = useMutation({ mutationFn: (id: number) => post(`/backups/${id}/send-offsite`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["backups"] }); setTimeout(() => qc.invalidateQueries({ queryKey: ["backups"] }), 4000); toast.success("Отправка запущена: файл шифруется и уходит в Telegram и на почту"); }, onError: (e: any) => toast.error(e.message) });
  const running = q.data?.items.some((j) => j.status === "running");
  const plan = q.data?.settings.offsite;
  const canAway = !!plan?.enabled && (plan.telegram || plan.email);

  return (
    <>
      <PageHeader title="Резервные копии" sub="Копия базы — страховка от поломки сервера и ошибок. Копия, которую ни разу не проверяли восстановлением, ничего не гарантирует"
        actions={<Button variant="primary" icon={Play} loading={start.isPending || !!running} onClick={() => start.mutate()}>{running ? "Идёт копирование…" : "Сделать копию сейчас"}</Button>} />
      {q.data?.warnings.map((w) => <div key={w} style={{ marginBottom: 12 }}><Alert tone="warning">{w}</Alert></div>)}
      <Alert tone="info">
        Автоматически копия делается раз в сутки в {String(q.data?.settings.hour ?? 3).padStart(2, "0")}:00 ({q.data?.settings.timezone ?? ""}), хранятся последние {q.data?.settings.keep ?? "—"} штук и
        последняя проверенная. Файлы лежат на этом же сервере, поэтому периодически скачивайте копию и храните её в другом месте.
      </Alert>
      {plan && (
        <div style={{ marginTop: 12 }}>
          {canAway
            ? <Alert tone="success">Копия вне сервера включена: после каждой копии зашифрованный файл уходит {[plan.telegram && `в Telegram (получателей: ${plan.telegram_chats})`, plan.email && `на почту (адресов: ${plan.email_to}, не больше ${plan.email_max_mb} МБ)`].filter(Boolean).join(" и ")}.</Alert>
            : <Alert tone="info">Копия вне сервера выключена. Чтобы файл копии сам уходил в Telegram или на почту, нужны пароль шифрования <span className="mono">OFFSITE_PASSWORD</span> и получатель (подключённый Telegram у того, кто видит копии, или <span className="mono">OFFSITE_EMAIL_TO</span>). Подробнее: страница «Telegram и почта».</Alert>}
        </div>
      )}
      <div style={{ margin: "16px 0" }}>
        <Card flush>
          <QueryState q={q}>
            {q.data && (q.data.items.length === 0 ? <Empty icon={Database} title="Копий ещё нет">Нажмите «Сделать копию сейчас».</Empty> : (
              <Table rows={q.data.items} rowKey={(j) => j.id}
                columns={[
                  { key: "t", header: "Когда", render: (j) => <><span className="nowrap">{fmtDateTime(j.started_at)}</span><div className="muted small">{j.trigger_label}{j.initiated_by ? ` · ${j.initiated_by}` : ""}</div></> },
                  { key: "s", header: "Копия", render: (j) => j.status === "running" ? <Badge tone="info" icon={Loader2}>Делается…</Badge> : j.status === "failed" ? <div><Badge tone="error">Не удалась</Badge><div className="muted small" style={{ maxWidth: 320 }}>{j.error}</div></div> : <Badge tone="success">Готова</Badge> },
                  { key: "z", header: "Размер", num: true, hideSm: true, render: (j) => (j.status === "ok" ? fmtSize(j.size_bytes) : "—") },
                  { key: "c", header: "В базе", hideSm: true, render: (j) => (j.status === "ok" ? `${j.tables} табл., ${fmtNum(j.rows_total, 0)} строк` : "—") },
                  { key: "v", header: "Проверка восстановлением", render: (j) => <><Verdict j={j} />{j.restore_check?.ok === false && j.restore_check.error && <div className="muted small" style={{ maxWidth: 320 }}>{j.restore_check.error}</div>}{j.restore_check?.mismatches?.length ? <div className="muted small">Расхождения в таблицах: {j.restore_check.mismatches.map((m) => m.table).join(", ")}</div> : null}{j.restore_checked_at && <div className="muted small">{fmtDateTime(j.restore_checked_at)}</div>}</> },
                  { key: "o", header: "Вне сервера", hideSm: true, render: (j) => <Away j={j} /> },
                  { key: "a", header: "", render: (j) => j.status === "ok" ? (
                    <div className="row wrap">
                      {j.file_available && canAway && <Button size="sm" icon={Send} disabled={away.isPending} onClick={() => away.mutate(j.id)}>Отправить вне сервера</Button>}
                      {j.file_available && <Button size="sm" icon={ShieldCheck} disabled={j.verifying} onClick={() => verify.mutate(j.id)}>Проверить</Button>}
                      {j.file_available ? <a className="btn sm" href={`/api/backups/${j.id}/download`}><Download size={14} aria-hidden /> Скачать</a> : <span className="muted small">{j.file_deleted ? "файл удалён" : "файла нет"}</span>}
                    </div>
                  ) : null },
                ]} />
            ))}
          </QueryState>
        </Card>
      </div>
      <Card title="Как восстановить базу из копии">
        <ol style={{ margin: 0, paddingLeft: 20 }} className="stack-sm">
          <li>Скачайте нужную копию (лучше ту, что отмечена «Восстановлена и сверена»).</li>
          <li>На сервере выполните <span className="mono">./scripts/restore.sh путь/к/копии.dump</span> — скрипт сначала переспросит, остановит программу, развернёт копию и запустит снова.</li>
          <li>Войдите и проверьте последние записи. Всё, что было введено после времени копии, придётся ввести заново.</li>
        </ol>
      </Card>
    </>
  );
}
