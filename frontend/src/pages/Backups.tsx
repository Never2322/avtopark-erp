import { Download, Database, Loader2, Play, ShieldCheck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { fmtDateTime, fmtNum, fmtSize } from "../format";
import { Alert, Badge, Button, Card, Empty, PageHeader, QueryState, Table, useToast } from "../ui";

interface Check { state: string; ok?: boolean; mismatches?: { table: string; before: number; after: number }[]; checked_tables?: number; schema_ok?: boolean; protective_triggers?: number; rows_total?: number; seconds?: number; error?: string }
interface Job {
  id: number; status: string; trigger_label: string; started_at: string; finished_at: string | null; filename: string | null; size_bytes: number | null; error: string | null;
  file_available: boolean; file_deleted: boolean; initiated_by: string | null; tables: number; rows_total: number; restore_checked_at: string | null;
  restore_check: Check | null; verified: boolean; verifying: boolean;
}
interface Data { items: Job[]; warnings: string[]; settings: { keep: number; schedule: string; hour: number; timezone: string } }

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
  const running = q.data?.items.some((j) => j.status === "running");

  return (
    <>
      <PageHeader title="Резервные копии" sub="Копия базы — страховка от поломки сервера и ошибок. Копия, которую ни разу не проверяли восстановлением, ничего не гарантирует"
        actions={<Button variant="primary" icon={Play} loading={start.isPending || !!running} onClick={() => start.mutate()}>{running ? "Идёт копирование…" : "Сделать копию сейчас"}</Button>} />
      {q.data?.warnings.map((w) => <div key={w} style={{ marginBottom: 12 }}><Alert tone="warning">{w}</Alert></div>)}
      <Alert tone="info">
        Автоматически копия делается раз в сутки в {String(q.data?.settings.hour ?? 3).padStart(2, "0")}:00 ({q.data?.settings.timezone ?? ""}), хранятся последние {q.data?.settings.keep ?? "—"} штук и
        последняя проверенная. Файлы лежат на этом же сервере, поэтому периодически скачивайте копию и храните её в другом месте.
      </Alert>
      <div style={{ marginTop: 16 }}>
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
                  { key: "a", header: "", render: (j) => j.status === "ok" ? (
                    <div className="row">
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
