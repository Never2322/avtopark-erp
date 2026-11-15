// Вкладка в карточке техники: список записей одного раздела (шины, ТО, ремонты…) только по этой машине.
// Поля и правила приходят с сервера (/meta), как и на общих экранах разделов.
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Inbox, Plus, type LucideIcon } from "lucide-react";
import { get, post } from "../api";
import { fmtMoney } from "../format";
import { Page } from "../hooks";
import { Button, Card, ConfirmModal, Empty, FilterChips, QueryState, Table, useToast, type Column } from "../ui";
import { cell, RecordModal, type Meta, type Row, type RowActions } from "../pages/Registry";

interface Listing extends Page<Row> { totals: Record<string, number> }

export function VehicleSection({ resource, vehicleId, icon, empty, actions }: { resource: string; vehicleId: number; icon?: LucideIcon; empty: string; actions?: RowActions }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [state, setState] = useState("");
  const [archived, setArchived] = useState(false);
  const [edit, setEdit] = useState<Row | "new" | null>(null);
  const [voiding, setVoiding] = useState<Row | null>(null);
  const [restoring, setRestoring] = useState<Row | null>(null);

  const meta = useQuery({ queryKey: ["reg-meta", resource], queryFn: () => get<Meta>(`/${resource}/meta`), staleTime: 60_000 });
  const m = meta.data;
  const params = { vehicle_id: vehicleId, state, archived, page_size: 100 };
  const list = useQuery({ queryKey: ["reg", resource, "vehicle", params], queryFn: () => get<Listing>(`/${resource}`, params), enabled: !!m, placeholderData: (p) => p });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["reg", resource] }); qc.invalidateQueries({ queryKey: ["reminders"] }); qc.invalidateQueries({ queryKey: ["notif-summary"] }); };

  const columns: Column<Row>[] = useMemo(() => !m ? [] : [
    ...m.fields.filter((f) => f.list && f.name !== "vehicle_id").map((f, i): Column<Row> => ({
      key: f.name, header: f.label, num: f.kind === "money" || f.kind === "int" || f.kind === "decimal", hideSm: i > 3,
      render: (r) => (i === 0 ? <strong>{cell(f, r)}</strong> : cell(f, r)),
    })),
    ...m.columns.filter((c) => !c.first).map((c): Column<Row> => ({ key: c.name, header: c.label, num: c.kind === "money" || c.kind === "number", render: (r) => cell(c, r) })),
    { key: "_a", header: "", width: 48 + (actions ? 40 * (list.data?.items.reduce((n, r) => Math.max(n, actions(r, refresh)?.width ?? 0), 0) ?? 0) : 0), render: (r) => (
      <span onClick={(e) => e.stopPropagation()} className="row-actions">
        {actions?.(r, refresh)?.node}
        {r.archived
          ? r.can_void && <Button variant="ghost" size="sm" icon={ArchiveRestore} aria-label="Вернуть из архива" title="Вернуть из архива" onClick={() => setRestoring(r)} />
          : r.can_void && <Button variant="ghost" size="sm" icon={Archive} aria-label="В архив" title="В архив" onClick={() => setVoiding(r)} />}
      </span>
    ) },
  ], [m, actions, list.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const totals = useMemo(() => {
    if (!m || !list.data) return [];
    const out: { label: string; value: number }[] = [];
    for (const f of m.fields) if (f.sum && f.name in list.data.totals) out.push({ label: f.label, value: list.data.totals[f.name] });
    for (const c of m.columns) if (c.sum && c.name in list.data.totals) out.push({ label: c.label, value: list.data.totals[c.name] });
    return out;
  }, [m, list.data]);

  const Icon = icon ?? Inbox;
  return (
    <Card flush title={m?.title ?? "…"} actions={m?.can.create && <Button variant="primary" size="sm" icon={Plus} onClick={() => setEdit("new")}>Добавить</Button>}>
      <QueryState q={meta}>
        {m && (
          <>
            <div className="toolbar" style={{ padding: "0 14px 12px" }}>
              {m.states.length > 0 && <FilterChips value={state} onChange={setState} options={[{ id: "", label: "Все" }, ...m.states.map((s) => ({ id: s.value, label: s.label }))]} />}
              <span className="spacer" />
              <label className="check"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Архив</label>
            </div>
            <QueryState q={list}>
              {list.data && (list.data.items.length === 0 ? (
                <Empty icon={Icon} title={state || archived ? "Ничего не найдено" : "Пока пусто"}>{!state && !archived ? empty : undefined}</Empty>
              ) : (
                <>
                  {totals.length > 0 && (
                    <div className="row wrap" style={{ padding: "0 14px 12px", gap: 18 }}>
                      {totals.map((t) => <span key={t.label} className="muted">{t.label.replace(/, ₽$/, "")}: <strong style={{ color: "var(--text)" }}>{fmtMoney(t.value)} ₽</strong></span>)}
                    </div>
                  )}
                  <Table rows={list.data.items} rowKey={(r) => r.id} dim={(r) => r.archived} columns={columns} onRowClick={(r) => setEdit(r)} />
                </>
              ))}
            </QueryState>
          </>
        )}
      </QueryState>
      {m && edit && <RecordModal meta={m} row={edit === "new" ? null : edit} hide={["vehicle_id"]} defaults={{ vehicle_id: String(vehicleId) }} onClose={() => setEdit(null)}
        onSaved={(warnings) => { refresh(); toast.success("Сохранено"); warnings.forEach((w) => toast.error(w)); }} />}
      {voiding && <ConfirmModal title="Убрать в архив" danger confirmLabel="В архив" reasonLabel="Причина" text="Запись не удаляется: её можно найти в архиве и вернуть."
        onConfirm={async (reason) => { await post(`/${resource}/${voiding.id}/void`, { reason }); refresh(); toast.success("Запись в архиве"); }} onClose={() => setVoiding(null)} />}
      {restoring && <ConfirmModal title="Вернуть из архива" confirmLabel="Вернуть"
        onConfirm={async () => { await post(`/${resource}/${restoring.id}/restore`); refresh(); toast.success("Запись возвращена"); }} onClose={() => setRestoring(null)} />}
    </Card>
  );
}
