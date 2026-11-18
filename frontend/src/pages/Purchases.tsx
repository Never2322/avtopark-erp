// «Что купить»: все детали из открытых обращений одним списком. Одинаковые детали сложены вместе, видно, для каких машин они нужны.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, CheckCircle2, Download, Search, ShoppingCart, Undo2 } from "lucide-react";
import { get, post, qs } from "../api";
import { fmtDateTime, plural } from "../format";
import { useDebounced, useEntityParam } from "../hooks";
import { Alert, Badge, Button, Card, Empty, PageHeader, QueryState, RequestStatus, useToast } from "../ui";

interface Line { part_id: number; request_id: number; number: number; status: string; status_label: string; approved: boolean; plate: string; vehicle_id: number; qty: number; qty_text: string; added_at: string; priority: string }
interface Group { name: string; article: string; unit: string; qty: number; qty_text: string; requests: number; ids: number[]; lines: Line[] }
interface Recent extends Line { name: string; article: string; unit: string; bought_at: string; bought_by: string | null; can_restore: boolean }
interface Data { items: Group[]; recent: Recent[]; recent_days: number; totals: { positions: number; lines: number; requests: number }; can: { mark: boolean; export: boolean; catalog: boolean } }

export function Purchases() {
  const entity = useEntityParam();
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const q = useDebounced(text);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const params = { q, legal_entity_id: entity };
  const list = useQuery({ queryKey: ["purchases", params], queryFn: () => get<Data>("/purchases", params), placeholderData: (p) => p });
  const d = list.data;

  const toggle = (ids: number[], on: boolean) => setPicked((s) => { const n = new Set(s); ids.forEach((i) => (on ? n.add(i) : n.delete(i))); return n; });
  const open = (k: string) => setOpened((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });
  const key = (g: Group) => `${g.name}|${g.article}|${g.unit}`;
  // отмеченное, чего уже нет в списке (обновили, кто-то отметил раньше), в запрос не берём
  const live = useMemo(() => new Set(d?.items.flatMap((g) => g.ids) ?? []), [d]);
  const ids = [...picked].filter((i) => live.has(i));

  const done = useMutation({
    mutationFn: () => post<{ marked: number; advanced: number[] }>("/purchases/bought", { ids }),
    onSuccess: (r) => {
      setPicked(new Set());
      qc.invalidateQueries();
      toast.success(`Отмечено купленным: ${r.marked}${r.advanced.length ? `. Обращения вернулись в работу: № ${r.advanced.join(", № ")}` : ""}`);
    },
    onError: (e: any) => { toast.error(e.message); qc.invalidateQueries({ queryKey: ["purchases"] }); },
  });
  const back = useMutation({
    mutationFn: (id: number) => post<{ moved_to_waiting: boolean }>("/purchases/restore", { id }),
    onSuccess: (r) => { qc.invalidateQueries(); toast.success(r.moved_to_waiting ? "Деталь снова в списке, обращение ждёт запчасти" : "Деталь снова в списке"); },
    onError: (e: any) => toast.error(e.message),
  });

  return (
    <>
      <PageHeader title="Что купить" sub={d ? `${d.totals.positions} ${plural(d.totals.positions, "позиция", "позиции", "позиций")} для ${d.totals.requests} ${plural(d.totals.requests, "обращения", "обращений", "обращений")}` : undefined}
        actions={d?.can.export && <a className="btn" href={`/api/purchases/export.xlsx${qs(params)}`}><Download size={16} aria-hidden /> Выгрузить в Excel</a>} />
      <Card flush>
        <div className="toolbar" style={{ padding: 14, marginBottom: 0 }}>
          <div style={{ position: "relative", minWidth: 240, flex: "1 1 240px", maxWidth: 380 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Деталь, артикул, госномер, № обращения" aria-label="Поиск по списку покупок" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
        </div>
        <QueryState q={list}>
          {d && (d.items.length === 0 ? (
            <Empty icon={ShoppingCart} title={q ? "Ничего не найдено" : "Покупать нечего"}>{q ? <Button onClick={() => setText("")}>Сбросить поиск</Button> : "Детали попадают сюда из открытых обращений. Всё, что в списках уже куплено, показано ниже."}</Empty>
          ) : (
            <ul className="buy-list">
              {d.items.map((g) => {
                const k = key(g), isOpen = opened.has(k) || g.lines.length === 1;
                const all = g.ids.every((i) => picked.has(i)), some = g.ids.some((i) => picked.has(i));
                return (
                  <li key={k}>
                    <div className="buy-head">
                      {d.can.mark && <input type="checkbox" aria-label={`Отметить «${g.name}»`} checked={all} ref={(el) => { if (el) el.indeterminate = some && !all; }} onChange={(e) => toggle(g.ids, e.target.checked)} />}
                      <button className="buy-title" onClick={() => open(k)} aria-expanded={isOpen}>
                        {g.lines.length > 1 ? (isOpen ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />) : <span style={{ width: 16 }} />}
                        <span><strong>{g.name}</strong>{g.article && <span className="muted small"> · арт. {g.article}</span>}</span>
                      </button>
                      <div className="buy-qty"><strong>{g.qty_text} {g.unit}</strong>{g.requests > 1 && <div className="muted small">для {g.requests} {plural(g.requests, "обращения", "обращений", "обращений")}</div>}</div>
                    </div>
                    {isOpen && (
                      <ul className="buy-lines">
                        {g.lines.map((ln) => (
                          <li key={ln.part_id}>
                            {d.can.mark && g.lines.length > 1 && <input type="checkbox" aria-label={`Отметить для ${ln.plate}`} checked={picked.has(ln.part_id)} onChange={(e) => toggle([ln.part_id], e.target.checked)} />}
                            <Link to={`/requests/${ln.request_id}`}><strong>{ln.plate}</strong> · обращение № {ln.number}</Link>
                            <RequestStatus code={ln.status} label={ln.status_label} />
                            {!ln.approved && <Badge tone="warning">ремонт ещё не согласован</Badge>}
                            <span className="grow" />
                            <span>{ln.qty_text} {g.unit}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          ))}
        </QueryState>
        {d?.can.mark && ids.length > 0 && (
          <div className="buy-bar">
            <span>Выбрано: <strong>{ids.length}</strong> {plural(ids.length, "строка", "строки", "строк")}</span>
            <Button variant="ghost" onClick={() => setPicked(new Set())}>Снять выбор</Button>
            <Button variant="primary" icon={CheckCircle2} loading={done.isPending} onClick={() => done.mutate()}>Куплено</Button>
          </div>
        )}
      </Card>
      {d && d.items.some((g) => g.lines.some((l) => !l.approved)) && <div style={{ marginTop: 12 }}><Alert tone="info">Деталь с пометкой «ремонт ещё не согласован» лучше не покупать, пока ремонт не одобрен: если его отклонят, деньги будут потрачены зря.</Alert></div>}
      {d && d.recent.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Card title={`Куплено за ${d.recent_days} ${plural(d.recent_days, "день", "дня", "дней")}`}>
            <ul className="buy-lines flat">
              {d.recent.map((r) => (
                <li key={r.part_id}>
                  <CheckCircle2 size={16} color="var(--success)" aria-hidden />
                  <div className="grow">
                    <strong>{r.name}</strong>{r.article && <span className="muted small"> · арт. {r.article}</span>} <span>— {r.qty_text} {r.unit}</span>
                    <div className="muted small"><Link to={`/requests/${r.request_id}`}>{r.plate}, обращение № {r.number}</Link> · {fmtDateTime(r.bought_at)}{r.bought_by ? `, ${r.bought_by}` : ""}</div>
                  </div>
                  {r.can_restore && <Button size="sm" icon={Undo2} loading={back.isPending && back.variables === r.part_id} onClick={() => back.mutate(r.part_id)}>Вернуть в список</Button>}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </>
  );
}
