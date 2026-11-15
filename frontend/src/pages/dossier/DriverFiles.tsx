// «Личные дела водителей»: список и карточка с фото, паспортом, правами, СТС и ПТС.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ImagePlus, Search, UserSquare2 } from "lucide-react";
import { get } from "../../api";
import { useAuth } from "../../auth";
import { fmtDate } from "../../format";
import { Page, useDebounced, useEntityParam, useUrlState } from "../../hooks";
import { Alert, Badge, Button, Card, Empty, FilterChips, PageHeader, Pagination, QueryState, Table } from "../../ui";
import { Avatar, Checklist, Doc, DocsBlock, ExpiryBadge, Expiry, Kind, NewDocModal, Summary, SummaryBadges } from "./shared";

interface Row {
  id: number; full_name: string; position: string; is_driver: boolean; archived: boolean; legal_entities: { id: number; name: string }[];
  photo_file_id: number | null; summary: Summary; license_until: string | null; license_expiry: Expiry;
}

export function DriverFiles() {
  const entity = useEntityParam();
  const nav = useNavigate();
  const [s, setS] = useUrlState({ q: "", who: "", problems: "", archived: "", page: "1" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);

  const params = { q: s.q, drivers_only: s.who !== "all", problems: s.problems === "1", archived: s.archived === "1", legal_entity_id: entity, page: Number(s.page), page_size: 25 };
  const list = useQuery({ queryKey: ["dossier-employees", params], queryFn: () => get<Page<Row>>("/dossier/employees", params), placeholderData: (p) => p });
  const multi = entity === undefined;

  return (
    <>
      <PageHeader title="Личные дела водителей" sub="Фото, паспорт, права, СТС и ПТС. Сроки документов проверяются автоматически" />
      <Card flush>
        <div className="toolbar" style={{ padding: "14px 14px 0" }}>
          <div style={{ position: "relative", minWidth: 240, flex: "1 1 240px", maxWidth: 360 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Фамилия, имя, должность" aria-label="Поиск по личным делам" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <label className="check"><input type="checkbox" checked={s.archived === "1"} onChange={(e) => setS({ archived: e.target.checked ? "1" : "" })} /> Архив</label>
        </div>
        <div className="row wrap" style={{ padding: "12px 14px", gap: 16 }}>
          <FilterChips value={s.who} onChange={(v) => setS({ who: v })} options={[{ id: "", label: "Водители" }, { id: "all", label: "Все сотрудники" }]} />
          <FilterChips value={s.problems} onChange={(v) => setS({ problems: v })} options={[{ id: "", label: "Все дела" }, { id: "1", label: "Есть просрочка или скоро срок" }]} />
        </div>
        <QueryState q={list}>
          {list.data && (list.data.items.length === 0 ? (
            <Empty icon={UserSquare2} title={s.q || s.problems ? "Ничего не найдено" : "Здесь пока никого нет"}>
              {!s.q && !s.problems && s.who !== "all" ? "В личные дела попадают сотрудники с пометкой «Водитель». Отметить её можно в разделе «Сотрудники»." : undefined}
            </Empty>
          ) : (
            <>
              <Table rows={list.data.items} rowKey={(r) => r.id} onRowClick={(r) => nav(`/dossier/employees/${r.id}`)} dim={(r) => r.archived}
                columns={[
                  { key: "a", header: "", width: 56, render: (r) => <Avatar fileId={r.photo_file_id} kind="person" /> },
                  { key: "n", header: "Сотрудник", render: (r) => <><strong>{r.full_name}</strong><div className="muted small">{r.position || (r.is_driver ? "Водитель" : "")}</div></> },
                  ...(multi ? [{ key: "e", header: "Юрлицо", hideSm: true, render: (r: Row) => r.legal_entities.map((x) => x.name).join(", ") || "—" }] : []),
                  { key: "l", header: "Права до", hideSm: true, render: (r) => (r.license_until ? <ExpiryBadge doc={{ kind: "driver_license", expiry: r.license_expiry, days_left: daysTo(r.license_until), valid_until: r.license_until }} /> : <span className="muted">не указаны</span>) },
                  { key: "d", header: "Документов", num: true, hideSm: true, render: (r) => r.summary.documents },
                  { key: "s", header: "Состояние", render: (r) => <SummaryBadges s={r.summary} /> },
                ]} />
              <Pagination page={list.data.page} pageSize={list.data.page_size} total={list.data.total} onPage={(p) => setS({ page: String(p) })} />
            </>
          ))}
        </QueryState>
      </Card>
    </>
  );
}

function daysTo(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  const end = Date.UTC(y, m - 1, d);
  const now = new Date();
  return Math.round((end - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
}

interface Detail {
  employee: {
    id: number; full_name: string; position: string; is_driver: boolean; archived: boolean; legal_entities: { id: number; name: string }[];
    phone?: string | null; email?: string | null; birth_date?: string | null; sensitive_hidden?: boolean; hired_on?: string | null;
    vehicles?: { id: number; plate: string; name: string }[];
  };
  photo_file_id: number | null; can_edit: boolean; can_passport: boolean; kinds: Kind[];
  documents: Doc[]; archived_documents: Doc[]; summary: Summary;
}

export function DriverFile() {
  const { id } = useParams();
  const { can } = useAuth();
  const [photo, setPhoto] = useState(false);
  const q = useQuery({ queryKey: ["dossier-employee", id], queryFn: () => get<Detail>(`/dossier/employees/${id}`) });
  const d = q.data;
  const e = d?.employee;
  return (
    <>
      <PageHeader
        crumb={<Link to="/dossier/employees"><ArrowLeft size={14} aria-hidden /> Личные дела водителей</Link>}
        title={e?.full_name ?? "Личное дело"}
        sub={e ? [e.position, e.legal_entities.map((x) => x.name).join(", ")].filter(Boolean).join(" · ") : undefined}
        actions={e && can("employees.view") ? <Link className="btn" to="/employees">Карточка в «Сотрудниках»</Link> : undefined} />
      <QueryState q={q} what="Сотрудник">
        {d && e && (
          <div className="stack">
            <Card>
              <div className="person-head">
                <div className="big-photo">
                  <Avatar fileId={d.photo_file_id} kind="person" size={132} />
                  {d.can_edit && <Button size="sm" icon={ImagePlus} onClick={() => setPhoto(true)}>{d.photo_file_id ? "Заменить фото" : "Добавить фото"}</Button>}
                </div>
                <div className="stack-sm" style={{ flex: 1, minWidth: 0 }}>
                  <div className="row wrap">
                    {e.is_driver && <Badge tone="info">Водитель</Badge>}
                    {e.archived && <Badge>В архиве</Badge>}
                    <SummaryBadges s={d.summary} />
                  </div>
                  <dl className="kv compact">
                    {!e.sensitive_hidden && e.phone !== undefined && <><dt>Телефон</dt><dd>{e.phone || "—"}</dd></>}
                    {!e.sensitive_hidden && e.birth_date !== undefined && <><dt>Дата рождения</dt><dd>{fmtDate(e.birth_date)}</dd></>}
                    {e.hired_on !== undefined && <><dt>Принят</dt><dd>{fmtDate(e.hired_on)}</dd></>}
                    {e.vehicles !== undefined && <><dt>Закреплена техника</dt><dd>{e.vehicles.length ? e.vehicles.map((v) => v.plate).join(", ") : <span className="muted">нет</span>}</dd></>}
                  </dl>
                  <Checklist kinds={d.kinds} docs={d.documents} />
                </div>
              </div>
            </Card>
            {!d.can_passport && (
              <Alert tone="info">Паспорта в этом деле вам не показываются: для них нужно отдельное право.</Alert>
            )}
            <Card title="Документы">
              <DocsBlock owner={{ type: "employees", id: e.id }} kinds={d.kinds} docs={d.documents} archived={d.archived_documents} canEdit={d.can_edit} />
            </Card>
          </div>
        )}
      </QueryState>
      {photo && d && e && <NewDocModal owner={{ type: "employees", id: e.id }} kinds={d.kinds} kind="photo" onClose={() => setPhoto(false)} />}
    </>
  );
}
