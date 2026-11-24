// Загрузка журнала тоннажа из Excel или CSV: выбрали файл, посмотрели, что получится, подтвердили.
// Понимает таблицу «Сводные данные по вывозу мусора»: Дата, Номер акта, г/н автомобиля, кол-во тн, стоимость.
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, FileUp, Upload } from "lucide-react";
import { upload, errorText } from "../api";
import { fmtDate, fmtMoney, fmtNum } from "../format";
import { useEntityParam } from "../hooks";
import { Alert, Badge, Button, Card, Field, FilterChips, PageHeader, Table, useToast, type Tone } from "../ui";

type Status = "ok" | "duplicate" | "error";
interface Row { n: number; plate: string; vehicle: string; date: string; doc_number: string; tons: number | null; price: number | null; cost: number | null; driver: string; status: Status; message: string }
interface Result {
  applied: boolean; columns: Record<string, string>; rows: Row[];
  summary: { total: number; ok: number; duplicate: number; error: number; tons: number; cost: number };
}

const STATUS: Record<Status, { label: string; tone: Tone }> = {
  ok: { label: "Добавится", tone: "success" },
  duplicate: { label: "Уже есть", tone: "neutral" },
  error: { label: "Ошибка", tone: "error" },
};
const COLS: Record<string, string> = { date: "дата", plate: "г/н", tons: "тонны", doc: "номер акта", price: "цена за тонну" };
const SHOWN = 300;
type Filter = "all" | Status;

export function TonnageImport() {
  const entity = useEntityParam();
  const qc = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<Result | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState("");

  const send = useMutation({
    mutationFn: (apply: boolean) => {
      const f = new FormData();
      f.append("file", file as File);
      f.append("apply", apply ? "1" : "0");
      if (entity) f.append("legal_entity_id", String(entity));
      return upload<Result>("/tonnage/import", f);
    },
    onSuccess: (r) => {
      setError("");
      setRes(r);
      setFilter(r.summary.error ? "error" : "all");
      if (r.applied) {
        toast.success(r.summary.ok ? `Добавлено талонов: ${r.summary.ok}` : "Ничего не добавлено");
        for (const key of ["reg", "report", "dashboard", "tonnage"]) qc.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (e) => setError(errorText(e)),
  });

  const reset = (next?: File | null) => { setRes(null); setError(""); if (next !== undefined) setFile(next); };
  const again = () => { reset(null); if (input.current) input.current.value = ""; };
  const pending = send.isPending;
  const s = res?.summary;
  const rows = res ? (filter === "all" ? res.rows : res.rows.filter((r) => r.status === filter)) : [];

  return (
    <>
      <PageHeader crumb={<Link to="/weigh-tickets">Журнал тоннажа</Link>} title="Загрузка тоннажа из Excel"
        sub="Талоны весов из таблицы Excel (.xlsx) или CSV: одна строка — один рейс. Сначала вы увидите, что получится, и только потом подтвердите загрузку" />
      <div className="stack">
        {!res?.applied && (
          <Card title="1. Файл">
            <Field label="Файл с тоннажем" hint="Excel .xlsx (берётся первый лист) или CSV, не больше 3 МБ">
              <input ref={input} className="input" type="file" accept=".csv,.txt,.xlsx" onChange={(e) => reset(e.target.files?.[0] ?? null)} />
            </Field>
            <p className="muted small" style={{ marginTop: 12 }}>
              Нужны колонки <strong>Дата</strong>, <strong>г/н автомобиля</strong>, <strong>кол-во тн</strong>; по желанию <strong>Номер акта</strong> и <strong>стоимость</strong> (цена за тонну).
              Заголовок может быть не в первой строке, строки «Итого» и пустые пропускаются. Если цены в файле нет, берётся цена за тонну из карточки машины.
              Водитель ставится тот, кто закреплён за машиной в день рейса. Повторная загрузка того же файла ничего не задвоит.
              {" "}<a href="/api/tonnage/import/template.csv"><Download size={14} aria-hidden style={{ verticalAlign: "-2px" }} /> Скачать образец</a>
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
              <Alert tone={s.ok ? "success" : "warning"}>
                <strong>{s.ok ? `Добавлено талонов: ${s.ok}, ${fmtNum(s.tons, 2)} т на ${fmtMoney(s.cost)} ₽.` : "Ничего не добавлено."}</strong>
                {s.duplicate > 0 && <> Уже были в журнале: {s.duplicate}.</>}
                {s.error > 0 && <> Пропущено из-за ошибок: {s.error} (список ниже).</>}
                {s.ok > 0 && <> <Link to="/weigh-tickets">Открыть журнал тоннажа</Link>.</>}
              </Alert>
            )}
            {!res.applied && s.ok > 0 && <Alert tone="info">Добавится талонов: <strong>{s.ok}</strong>, всего <strong>{fmtNum(s.tons, 2)} т</strong> на <strong>{fmtMoney(s.cost)} ₽</strong>.</Alert>}
            {!res.applied && s.error > 0 && <Alert tone="warning">В файле есть строки с ошибками ({s.error}). При загрузке они будут <strong>пропущены</strong>, остальные добавятся.</Alert>}
            {!res.applied && s.ok === 0 && <Alert tone="info">Добавлять нечего: {s.duplicate && !s.error ? "все талоны уже есть в журнале." : "ни одна строка не подошла."}</Alert>}
            <div className="row wrap" style={{ margin: "12px 0" }}>
              <FilterChips<Filter> value={filter} onChange={setFilter} options={[
                { id: "all", label: "Все", count: s.total },
                { id: "error", label: "Ошибки", count: s.error },
                { id: "duplicate", label: "Уже есть", count: s.duplicate },
                { id: "ok", label: "Добавятся", count: s.ok },
              ]} />
            </div>
            <p className="muted small" style={{ margin: "0 0 10px" }}>
              Колонки найдены: {Object.entries(res.columns).map(([k, v]) => `${COLS[k] ?? k} ← «${v}»`).join(", ")}
            </p>
            <Table rows={rows.slice(0, SHOWN)} rowKey={(r) => r.n}
              columns={[
                { key: "n", header: "Строка", num: true, hideSm: true, render: (r) => r.n },
                { key: "d", header: "Дата", render: (r) => (r.date ? fmtDate(r.date) : "—") },
                { key: "v", header: "Машина", render: (r) => <strong>{(r.vehicle || r.plate || "—").split(" · ")[0]}</strong> },
                { key: "doc", header: "Акт", hideSm: true, render: (r) => r.doc_number || "—" },
                { key: "t", header: "Тонн", num: true, render: (r) => (r.tons === null ? "—" : fmtNum(r.tons, 2)) },
                { key: "p", header: "Цена за т", num: true, hideSm: true, render: (r) => (r.price === null ? "—" : fmtMoney(r.price)) },
                { key: "c", header: "Стоимость", num: true, render: (r) => (r.cost === null ? "—" : fmtMoney(r.cost)) },
                { key: "w", header: "Водитель", hideSm: true, render: (r) => r.driver || "—" },
                { key: "st", header: "Результат", render: (r) => <Badge tone={STATUS[r.status].tone}>{res.applied && r.status === "ok" ? "Добавлено" : STATUS[r.status].label}</Badge> },
                { key: "m", header: "Пояснение", render: (r) => <span className={r.status === "error" ? "" : "muted"}>{r.message || "—"}</span> },
              ]} />
            {rows.length > SHOWN && <p className="muted small" style={{ marginTop: 8 }}>Показаны первые {SHOWN} из {rows.length} строк.</p>}
            {rows.length === 0 && <p className="muted" style={{ marginTop: 8 }}>В этом отборе строк нет.</p>}
            <div className="row wrap" style={{ marginTop: 16 }}>
              {res.applied
                ? <Button icon={Upload} onClick={again}>Загрузить другой файл</Button>
                : <>
                    <Button variant="primary" icon={CheckCircle2} loading={pending} disabled={!s.ok || pending} onClick={() => send.mutate(true)}>Загрузить талонов: {s.ok}</Button>
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
