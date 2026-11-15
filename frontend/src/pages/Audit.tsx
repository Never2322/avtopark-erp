import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, History, Search } from "lucide-react";
import { get } from "../api";
import { fmtDateTime } from "../format";
import { Page, useDebounced, useEntityParam, useUrlState } from "../hooks";
import { Button, Card, Empty, PageHeader, Pagination, QueryState, Table } from "../ui";

interface Row { id: number; at: string; user_login: string; action: string; action_label: string; entity_type: string; entity_id: number | null; ip: string; details: Record<string, unknown> }

const KEYS: Record<string, string> = {
  login: "логин", number: "номер", vehicle: "машина", plate: "госномер", name: "название", reason: "причина", comment: "комментарий", changed: "изменено",
  from_status: "было", to_status: "стало", status: "статус", value: "значение", previous: "было", fields: "поля", ok: "успешно", error: "ошибка",
  sessions_ended: "завершено сеансов", filename: "файл", roles_before: "роли до", roles_after: "роли после", active_before: "работал до", active_after: "работает после",
  added: "добавлено", removed: "убрано", role: "роль", permissions: "права", start_km: "пробег на начало", end_km: "пробег на конец", start_hours: "моточасы на начало",
  end_hours: "моточасы на конец", needs_review: "нужна проверка", priority: "срочность", driver_id: "водитель №", employee_id: "сотрудник №", mismatches: "расхождений",
};
const text = (v: unknown): string => (Array.isArray(v) ? v.map(text).join(", ") : v !== null && typeof v === "object" ? JSON.stringify(v) : v === true ? "да" : v === false ? "нет" : String(v ?? "—"));

function Details({ d }: { d: Record<string, unknown> }) {
  const entries = Object.entries(d ?? {}).filter(([, v]) => v !== "" && v !== null && !(Array.isArray(v) && v.length === 0));
  if (!entries.length) return <span className="muted">Подробностей нет</span>;
  return <div className="details">{entries.map(([k, v]) => `${KEYS[k] ?? k}: ${text(v)}`).join("\n")}</div>;
}

export function Audit() {
  const entity = useEntityParam();
  const [s, setS] = useUrlState({ q: "", action: "", user: "", date_from: "", date_to: "", page: "1" });
  const [qText, setQText] = useState(s.q);
  const [uText, setUText] = useState(s.user);
  const dq = useDebounced(qText), du = useDebounced(uText);
  const [openId, setOpenId] = useState<number | null>(null);
  useEffect(() => { if (dq !== s.q) setS({ q: dq }); }, [dq]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (du !== s.user) setS({ user: du }); }, [du]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setQText(s.q); setUText(s.user); }, [s.q, s.user]);

  const actions = useQuery({ queryKey: ["audit-actions"], queryFn: () => get<{ items: { code: string; label: string }[] }>("/audit/actions"), staleTime: 10 * 60_000 });
  const params = { q: s.q, action: s.action, user: s.user, date_from: s.date_from, date_to: s.date_to, legal_entity_id: entity, page: Number(s.page), page_size: 50 };
  const list = useQuery({ queryKey: ["audit", params], queryFn: () => get<Page<Row>>("/audit", params), placeholderData: (p) => p });
  const filtered = !!(s.q || s.action || s.user || s.date_from || s.date_to);

  return (
    <>
      <PageHeader title="Журнал действий" sub="Кто, когда и что сделал. Записи нельзя менять или удалять" />
      <Card flush>
        <div className="toolbar" style={{ padding: 14 }}>
          <div style={{ position: "relative", minWidth: 200, flex: "1 1 200px", maxWidth: 300 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Поиск" aria-label="Поиск по журналу" value={qText} onChange={(e) => setQText(e.target.value)} />
          </div>
          <input className="input compact" placeholder="Логин" aria-label="Логин пользователя" value={uText} onChange={(e) => setUText(e.target.value)} />
          <select className="input compact" aria-label="Действие" value={s.action} onChange={(e) => setS({ action: e.target.value })}>
            <option value="">Любое действие</option>
            {actions.data?.items.map((a) => <option key={a.code} value={a.code}>{a.label}</option>)}
          </select>
          <label className="row" style={{ gap: 6 }}><span className="muted small">С</span><input className="input compact" type="date" aria-label="Дата с" value={s.date_from} onChange={(e) => setS({ date_from: e.target.value })} /></label>
          <label className="row" style={{ gap: 6 }}><span className="muted small">По</span><input className="input compact" type="date" aria-label="Дата по" value={s.date_to} onChange={(e) => setS({ date_to: e.target.value })} /></label>
          {filtered && <Button size="sm" onClick={() => { setQText(""); setUText(""); setS({ q: "", user: "", action: "", date_from: "", date_to: "" }); }}>Сбросить</Button>}
        </div>
        <QueryState q={list}>
          {list.data && (list.data.items.length === 0 ? <Empty icon={History} title={filtered ? "Ничего не найдено" : "Записей пока нет"} /> : (
            <>
              <Table rows={list.data.items} rowKey={(r) => r.id}
                columns={[
                  { key: "t", header: "Когда", render: (r) => <span className="nowrap">{fmtDateTime(r.at)}</span> },
                  { key: "u", header: "Кто", render: (r) => r.user_login || "—" },
                  { key: "a", header: "Что сделал", render: (r) => (
                    <div>
                      <button className="btn ghost sm" style={{ padding: "2px 6px", marginLeft: -6 }} aria-expanded={openId === r.id} onClick={() => setOpenId(openId === r.id ? null : r.id)}>
                        {openId === r.id ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}{r.action_label}
                      </button>
                      {openId === r.id && <Details d={r.details} />}
                    </div>
                  ) },
                  { key: "o", header: "Объект", hideSm: true, render: (r) => (r.entity_type ? <span className="muted">{r.entity_type}{r.entity_id ? ` №${r.entity_id}` : ""}</span> : "—") },
                  { key: "ip", header: "Адрес", hideSm: true, render: (r) => <span className="muted mono">{r.ip || "—"}</span> },
                ]} />
              <Pagination page={list.data.page} pageSize={list.data.page_size} total={list.data.total} onPage={(p) => setS({ page: String(p) })} />
            </>
          ))}
        </QueryState>
      </Card>
    </>
  );
}
