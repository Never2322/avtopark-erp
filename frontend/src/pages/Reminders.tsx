// Напоминания: что уже наступило и что наступит в ближайшие дни. Собирается из разделов, на которые у человека есть право.
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BellRing, CalendarDays, Download } from "lucide-react";
import { get, qs } from "../api";
import { useEntityParam, useUrlState } from "../hooks";
import { fmtDate } from "../format";
import { Badge, Empty, FilterChips, PageHeader, QueryState, Table } from "../ui";

export interface Reminder {
  source: string; source_label: string; level: "due" | "soon"; tone: "error" | "warning"; day: string; left: number; left_text: string;
  title: string; subject: string; url: string; entity_id: number | null; ref_id: number | null;
}
interface Listing {
  items: Reminder[]; counts: { due: number; soon: number; total: number }; sources: { code: string; label: string; count: number }[]; levels: Record<string, string>;
}

export function Reminders() {
  const entity = useEntityParam();
  const [f, setF] = useUrlState({ level: "", source: "" });
  const params = { level: f.level, source: f.source, legal_entity_id: entity };
  const q = useQuery({ queryKey: ["reminders", params], queryFn: () => get<Listing>("/reminders", params), placeholderData: (p) => p, refetchInterval: 5 * 60_000 });
  const d = q.data;
  return (
    <>
      <PageHeader title="Напоминания" sub="Что уже наступило и что наступит в ближайшие дни: сроки документов, штрафы, аренда, СИЗ, события сотрудников, неоплаченные счета и ваши напоминания. Видно только то, к чему у вас есть доступ"
        actions={<>
          <Link className="btn" to="/calendar"><CalendarDays size={16} aria-hidden /> Календарь</Link>
          {d && d.counts.total > 0 && <a className="btn" href={`/api/reminders/export.xlsx${qs({ legal_entity_id: entity })}`} download><Download size={16} aria-hidden /> В Excel</a>}
        </>} />
      <div className="toolbar">
        <FilterChips value={f.level} onChange={(v) => setF({ level: v })}
          options={[{ id: "", label: "Все", count: d?.counts.total }, { id: "due", label: "Наступило", count: d?.counts.due }, { id: "soon", label: "Скоро", count: d?.counts.soon }]} />
        {d && d.sources.length > 1 && (
          <select className="input compact" aria-label="Раздел" value={f.source} onChange={(e) => setF({ source: e.target.value })}>
            <option value="">Все разделы</option>
            {d.sources.map((s) => <option key={s.code} value={s.code}>{s.label} ({s.count})</option>)}
          </select>
        )}
      </div>
      <QueryState q={q}>
        {d && (d.items.length === 0
          ? <Empty icon={BellRing} title={d.counts.total ? "По этому отбору ничего нет" : "Пока всё в порядке"}>
              {d.counts.total ? "Снимите отбор, чтобы увидеть остальные напоминания." : "Просроченного нет и в ближайшие дни ничего не истекает."}
            </Empty>
          : <Table rows={d.items} rowKey={(r) => `${r.source}-${r.ref_id}-${r.day}-${r.title}`}
              columns={[
                { key: "s", header: "Состояние", render: (r) => <Badge tone={r.tone}>{r.level === "due" ? "Наступило" : "Скоро"}</Badge> },
                { key: "t", header: "Что", render: (r) => (
                  <>
                    {r.url ? <Link to={r.url}><strong>{r.title}</strong></Link> : <strong>{r.title}</strong>}
                    {r.subject && <span className="only-sm muted small">{r.subject}</span>}
                  </>
                ) },
                { key: "w", header: "Кто или что", hideSm: true, render: (r) => r.subject || <span className="muted">—</span> },
                { key: "k", header: "Раздел", hideSm: true, render: (r) => <span className="muted">{r.source_label}</span> },
                { key: "d", header: "Срок", render: (r) => <><span className="nowrap">{fmtDate(r.day)}</span><span className="muted small nowrap" style={{ display: "block" }}>{r.left_text}</span></> },
              ]} />)}
      </QueryState>
    </>
  );
}
