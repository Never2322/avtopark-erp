// Ручной ввод спидометра (и моточасов) списком: вся техника, по которой человек может вводить показания.
// Водитель видит свою закреплённую технику, директор и диспетчер — любую технику своих юрлиц.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Gauge, Save, Search } from "lucide-react";
import { get, post, errorText } from "../api";
import { fmtNum, todayIso } from "../format";
import { useEntityParam } from "../hooks";
import { Alert, Badge, Button, Card, Empty, QueryState, useToast } from "../ui";

interface Item { id: number; plate: string; name: string; primary: "km" | "engine_hours"; current_km: number | null; current_hours: number | null }
interface Res { vehicle_id: number; ok: boolean; message?: string; needs_review?: boolean }

const parse = (s: string): number | null => {
  const t = s.replace(/\s/g, "").replace(",", ".");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 10) / 10 : NaN;
};

export function ManualReadings() {
  const entity = useEntityParam();
  const qc = useQueryClient();
  const toast = useToast();
  const today = todayIso();
  const q = useQuery({ queryKey: ["readings-manual", entity], queryFn: () => get<{ items: Item[]; all: boolean }>("/readings/manual", { legal_entity_id: entity }) });
  const [vals, setVals] = useState<Record<string, string>>({});
  const [day, setDay] = useState(today);
  const [find, setFind] = useState("");
  const [res, setRes] = useState<Record<number, Res>>({});

  const key = (id: number, kind: string) => `${id}:${kind}`;
  const items = useMemo(() => {
    const t = find.trim().toLowerCase().replace(/\s/g, "");
    return (q.data?.items ?? []).filter((v) => !t || `${v.plate}${v.name}`.toLowerCase().replace(/\s/g, "").includes(t));
  }, [q.data, find]);
  const filled = Object.entries(vals).filter(([, s]) => s.trim() !== "");
  const bad = filled.filter(([, s]) => Number.isNaN(parse(s)));

  const save = useMutation({
    mutationFn: () => {
      const at = day === today ? null : new Date(`${day}T12:00:00`).toISOString();
      return post<{ results: Res[]; saved: number }>("/readings/manual", {
        items: filled.map(([k, s]) => { const [id, kind] = k.split(":"); return { vehicle_id: Number(id), kind, value: parse(s), recorded_at: at }; }),
      });
    },
    onSuccess: (r) => {
      const byId: Record<number, Res> = {};
      for (const x of r.results) byId[x.vehicle_id] = x;
      setRes(byId);
      // записанное убираем из полей, с ошибкой — оставляем, чтобы поправить
      setVals((old) => Object.fromEntries(Object.entries(old).filter(([k]) => !byId[Number(k.split(":")[0])]?.ok)));
      const failed = r.results.length - r.saved;
      if (r.saved) toast.success(`Сохранено показаний: ${r.saved}`);
      if (failed) toast.error(`Не сохранено: ${failed}. Причина — в строке машины`);
      for (const k of ["readings-manual", "vehicles", "vehicle", "readings", "readings-pending", "dashboard"]) qc.invalidateQueries({ queryKey: [k] });
    },
    onError: (e) => toast.error(errorText(e)),
  });

  const input = (v: Item, kind: "km" | "engine_hours") => (
    <input className="input compact num-input" inputMode="decimal" aria-label={`${kind === "km" ? "Спидометр" : "Моточасы"}: ${v.plate}`}
      placeholder={kind === "km" ? (v.current_km !== null ? fmtNum(v.current_km, 0) : "км") : (v.current_hours !== null ? fmtNum(v.current_hours, 1) : "м/ч")}
      value={vals[key(v.id, kind)] ?? ""} onChange={(e) => { setVals((o) => ({ ...o, [key(v.id, kind)]: e.target.value })); setRes((o) => { const n = { ...o }; delete n[v.id]; return n; }); }} />
  );

  return (
    <Card title="Ввести показания вручную" icon={Gauge}>
      <QueryState q={q}>
        {q.data && (q.data.items.length === 0 ? <Empty icon={Gauge} title="Нет техники для ввода показаний">За вами не закреплена техника. Попросите диспетчера закрепить машину.</Empty> : (
          <div className="stack-sm">
            <p className="muted small" style={{ margin: 0 }}>
              {q.data.all ? "Здесь вся техника ваших юридических лиц." : "Здесь техника, закреплённая за вами."} Впишите показание спидометра (и моточасы, если считаются) у нужных машин и нажмите «Сохранить».
              Пустые строки не сохраняются. Показание не может быть меньше прежнего, большой скачок уходит на проверку.
            </p>
            <div className="toolbar">
              <div style={{ position: "relative", minWidth: 200, flex: "1 1 200px", maxWidth: 320 }}>
                <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
                <input className="input" style={{ paddingLeft: 34 }} placeholder="Госномер или марка" aria-label="Поиск техники" value={find} onChange={(e) => setFind(e.target.value)} />
              </div>
              <label className="row" style={{ gap: 6 }}><span className="muted small">Дата показаний</span>
                <input className="input compact" type="date" aria-label="Дата показаний" value={day} max={today} onChange={(e) => e.target.value && setDay(e.target.value)} /></label>
            </div>
            {bad.length > 0 && <Alert tone="error">Не число в {bad.length} {bad.length === 1 ? "поле" : "полях"}: впишите, например, 152340 или 4812,5.</Alert>}
            <div className="table-wrap">
              <table className="table manual-readings">
                <thead><tr><th>Техника</th><th className="num">Сейчас</th><th>Спидометр, км</th><th>Моточасы</th><th>Итог</th></tr></thead>
                <tbody>
                  {items.map((v) => {
                    const r = res[v.id];
                    return (
                      <tr key={v.id}>
                        <td><Link to={`/vehicles/${v.id}`}><strong>{v.plate}</strong></Link><div className="muted small">{v.name}</div></td>
                        <td className="num nowrap">{v.current_km !== null ? `${fmtNum(v.current_km, 0)} км` : "—"}{v.current_hours !== null && <div className="muted small">{fmtNum(v.current_hours, 1)} м/ч</div>}</td>
                        <td>{input(v, "km")}</td>
                        <td>{v.primary === "engine_hours" || v.current_hours !== null ? input(v, "engine_hours") : <span className="muted small">не считаются</span>}</td>
                        <td>{r ? (r.ok ? <Badge tone={r.needs_review ? "warning" : "success"}>{r.needs_review ? "На проверку" : "Сохранено"}</Badge> : <span className="bad small">{r.message}</span>) : null}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="row wrap">
              <Button variant="primary" icon={Save} loading={save.isPending} disabled={!filled.length || bad.length > 0 || save.isPending} onClick={() => save.mutate()}>
                Сохранить{filled.length ? `: ${filled.length}` : ""}
              </Button>
            </div>
          </div>
        ))}
      </QueryState>
    </Card>
  );
}
