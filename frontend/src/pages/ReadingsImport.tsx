// Загрузка показаний пробега и моточасов из файла: выбрали файл, посмотрели, что получится, подтвердили.
// Просмотр и загрузка считаются одним и тем же кодом на сервере, поэтому «что показали» и «что добавлено» совпадают.
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, FileUp, Upload } from "lucide-react";
import { upload, errorText } from "../api";
import { fmtDate, fmtNum, readingUnit } from "../format";
import { useEntityParam } from "../hooks";
import { Alert, Badge, Button, Card, Field, FilterChips, PageHeader, Table, useToast, type Tone } from "../ui";
import { useAuth } from "../auth";
import { ManualReadings } from "./ManualReadings";

interface Row { n: number; plate: string; vehicle_id: number | null; vehicle: string; kind: string; date: string; value: number | null; status: Status; message: string }
type Status = "ok" | "review" | "duplicate" | "error";
interface Result {
  applied: boolean; default_kind: string; columns: Record<string, string>; rows: Row[];
  summary: { total: number; ok: number; review: number; duplicate: number; error: number };
}

const STATUS: Record<Status, { label: string; tone: Tone }> = {
  ok: { label: "Добавится", tone: "success" },
  review: { label: "На проверку", tone: "warning" },
  duplicate: { label: "Уже есть", tone: "neutral" },
  error: { label: "Ошибка", tone: "error" },
};
const SHOWN = 300;

type Filter = "all" | Status;

/** «Ввод показаний»: вручную списком (все, кто вводит показания) и загрузка из файла (директор и диспетчер). */
export function ReadingsImport() {
  const { can } = useAuth();
  const manual = can("readings.create") || can("readings.create_own");
  const fromFile = can("readings.import");
  const [tab, setTab] = useState<"manual" | "file">(manual ? "manual" : "file");
  return (
    <>
      <PageHeader title="Ввод показаний" sub="Спидометр и моточасы: вручную по каждой машине или загрузкой из файла CSV или Excel" />
      {manual && fromFile && (
        <div className="row wrap" style={{ marginBottom: 12 }}>
          <FilterChips<"manual" | "file"> value={tab} onChange={setTab} options={[{ id: "manual", label: "Вручную" }, { id: "file", label: "Из файла" }]} />
        </div>
      )}
      {tab === "manual" ? <ManualReadings /> : <ReadingsFromFile />}
    </>
  );
}

function ReadingsFromFile() {
  const entity = useEntityParam();
  const qc = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("km");
  const [res, setRes] = useState<Result | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState("");

  const send = useMutation({
    mutationFn: (apply: boolean) => {
      const f = new FormData();
      f.append("file", file as File);
      f.append("kind", kind);
      f.append("apply", apply ? "1" : "0");
      if (entity) f.append("legal_entity_id", String(entity));
      return upload<Result>("/readings/import", f);
    },
    onSuccess: (r) => {
      setError("");
      setRes(r);
      setFilter(r.summary.error ? "error" : "all");
      if (r.applied) {
        const added = r.summary.ok + r.summary.review;
        toast.success(added ? `Добавлено показаний: ${added}` : "Ничего не добавлено");
        for (const key of ["vehicles", "vehicle", "readings", "readings-pending", "dashboard", "reports"]) qc.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (e) => { setError(errorText(e)); },
  });

  const reset = (next?: File | null) => { setRes(null); setError(""); if (next !== undefined) setFile(next); };
  const again = () => { reset(null); if (input.current) input.current.value = ""; };
  const pending = send.isPending;
  const s = res?.summary;
  const addable = s ? s.ok + s.review : 0;
  const rows = res ? (filter === "all" ? res.rows : res.rows.filter((r) => r.status === filter)) : [];

  return (
    <>
      <p className="muted small" style={{ margin: "0 0 12px" }}>Файл CSV или Excel (.xlsx): одна строка — одно показание. Сначала вы увидите, что получится, и только потом подтвердите загрузку.</p>
      <div className="stack">
        {!res?.applied && (
          <Card title="1. Файл">
            <div className="form-grid">
              <Field label="Файл с показаниями" hint="CSV (запятая, точка с запятой или табуляция) или Excel .xlsx, не больше 3 МБ и 5000 строк">
                <input ref={input} className="input" type="file" accept=".csv,.txt,.xlsx" onChange={(e) => reset(e.target.files?.[0] ?? null)} />
              </Field>
              <Field label="Что в файле, если не указано в колонке «Вид»">
                <select className="input" value={kind} onChange={(e) => { setKind(e.target.value); setRes(null); }}>
                  <option value="km">Пробег, км</option>
                  <option value="engine_hours">Моточасы, ч</option>
                </select>
              </Field>
            </div>
            <p className="muted small" style={{ marginTop: 12 }}>
              В первой строке нужны названия колонок: <strong>Госномер</strong>, <strong>Дата</strong>, <strong>Показание</strong>; по желанию <strong>Вид</strong> («км» или «моточасы») и <strong>Примечание</strong>.
              Дата в виде 31.12.2026 или 2026-12-31, можно со временем (14:30); без времени ставится полдень. Правила те же, что и при ручном вводе: показание не меньше предыдущего и не в будущем, большой скачок уходит на проверку.
              {" "}<a href="/api/readings/import/template.csv"><Download size={14} aria-hidden style={{ verticalAlign: "-2px" }} /> Скачать образец</a>
            </p>
            {error && <div style={{ marginTop: 12 }}><Alert tone="error">{error}</Alert></div>}
            <div className="row wrap" style={{ marginTop: 14 }}>
              <Button variant="primary" icon={FileUp} loading={pending && !res} disabled={!file || pending} onClick={() => send.mutate(false)}>Посмотреть, что получится</Button>
              {entity ? <span className="muted small">Берутся машины только выбранного вверху юридического лица</span> : null}
            </div>
          </Card>
        )}

        {res && s && (
          <Card title={res.applied ? "Готово" : "2. Что получится"}>
            {res.applied && (
              <Alert tone={addable ? "success" : "warning"}>
                <strong>{addable ? `Добавлено показаний: ${addable}.` : "Ничего не добавлено."}</strong>
                {s.review > 0 && <> Из них {s.review} ушли на проверку из-за большого скачка: <Link to="/readings">открыть очередь</Link>.</>}
                {s.duplicate > 0 && <> Уже были в программе: {s.duplicate}.</>}
                {s.error > 0 && <> Пропущено из-за ошибок: {s.error} (список ниже).</>}
              </Alert>
            )}
            {!res.applied && s.error > 0 && <Alert tone="warning">В файле есть строки с ошибками ({s.error}). При загрузке они будут <strong>пропущены</strong>, остальные добавятся. Можно исправить файл и посмотреть ещё раз.</Alert>}
            {!res.applied && addable === 0 && <Alert tone="info">Добавлять нечего: {s.duplicate && !s.error ? "все показания уже есть в программе." : "ни одна строка не подошла."}</Alert>}
            <div className="row wrap" style={{ margin: "12px 0" }}>
              <FilterChips<Filter> value={filter} onChange={setFilter} options={[
                { id: "all", label: "Все", count: s.total },
                { id: "error", label: "Ошибки", count: s.error },
                { id: "review", label: "На проверку", count: s.review },
                { id: "duplicate", label: "Уже есть", count: s.duplicate },
                { id: "ok", label: "Добавятся", count: s.ok },
              ]} />
            </div>
            <p className="muted small" style={{ margin: "0 0 10px" }}>
              Колонки найдены: {Object.entries(res.columns).map(([k, v]) => `${({ plate: "госномер", date: "дата", value: "показание", kind: "вид", note: "примечание" } as Record<string, string>)[k]} ← «${v}»`).join(", ")}
            </p>
            <Table rows={rows.slice(0, SHOWN)} rowKey={(r) => r.n}
                columns={[
                  { key: "n", header: "Строка", num: true, hideSm: true, render: (r) => r.n },
                  { key: "v", header: "Машина", render: (r) => (r.vehicle_id ? <><Link to={`/vehicles/${r.vehicle_id}`}><strong>{r.vehicle.split(" · ")[0]}</strong></Link><span className="muted small"> {r.vehicle.split(" · ")[1]}</span></> : <strong>{r.plate || "—"}</strong>) },
                  { key: "d", header: "Дата", render: (r) => (r.date ? fmtDate(r.date.slice(0, 10)) + r.date.slice(10) : "—") },
                  { key: "val", header: "Показание", num: true, render: (r) => (r.value === null ? "—" : `${fmtNum(r.value)} ${readingUnit(r.kind)}`) },
                  { key: "st", header: "Результат", render: (r) => <Badge tone={STATUS[r.status].tone}>{res.applied && r.status === "ok" ? "Добавлено" : STATUS[r.status].label}</Badge> },
                  { key: "m", header: "Пояснение", render: (r) => <span className={r.status === "error" ? "" : "muted"}>{r.message || "—"}</span> },
                ]} />
            {rows.length > SHOWN && <p className="muted small" style={{ marginTop: 8 }}>Показаны первые {SHOWN} из {rows.length} строк. Выберите отбор выше, например «Ошибки».</p>}
            {rows.length === 0 && <p className="muted" style={{ marginTop: 8 }}>В этом отборе строк нет.</p>}
            <div className="row wrap" style={{ marginTop: 16 }}>
              {res.applied
                ? <Button icon={Upload} onClick={again}>Загрузить другой файл</Button>
                : <>
                    <Button variant="primary" icon={CheckCircle2} loading={pending} disabled={!addable || pending} onClick={() => send.mutate(true)}>
                      Загрузить показаний: {addable}
                    </Button>
                    <Button onClick={again} disabled={pending}>Другой файл</Button>
                  </>}
            </div>
            {error && <div style={{ marginTop: 12 }}><Alert tone="error">{error}</Alert></div>}
          </Card>
        )}
      </div>
    </>
  );
}
