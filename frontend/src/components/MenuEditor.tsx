// Настройка меню слева: директор и администратор перетаскивают группы и пункты куда угодно и меняют их названия.
// Настройка одна на всю программу и хранится на сервере. Какие пункты кому видны, по-прежнему решают права: здесь только порядок,
// группа и название. Новые пункты из следующих версий программы сами встают на своё место по умолчанию.
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Check, GripVertical, RotateCcw, X, type LucideIcon } from "lucide-react";
import { api, errorText, put } from "../api";
import { Button, ConfirmModal, useToast } from "../ui";
import type { Group, Item } from "./Shell";

export interface LayoutItem { id: string; label: string; sub: string }
export interface LayoutGroup { key: string; title: string; items: LayoutItem[] }
export interface Layout { groups: LayoutGroup[] }
export interface MenuLayoutRes { layout: Layout | null; can_edit: boolean }

export const itemId = (i: Item) => i.id ?? i.to;

/** Меню по умолчанию + сохранённая настройка. Незнакомые ключи пропускаются, пункты, которых в настройке нет, встают на место по умолчанию. */
export function applyLayout(nav: Group[], layout: Layout | null | undefined): Group[] {
  if (!layout?.groups?.length) return nav;
  const known = new Map<string, Item>();
  nav.forEach((g) => g.items.forEach((i) => known.set(itemId(i), i)));
  const used = new Set<string>();
  const out: Group[] = [];
  for (const lg of layout.groups) {
    const g = nav.find((x) => x.key === lg.key);
    if (!g || out.some((o) => o.key === g.key)) continue;
    const items: Item[] = [];
    for (const li of lg.items) {
      const i = known.get(li.id);
      if (!i || used.has(li.id)) continue;
      used.add(li.id);
      items.push({ ...i, label: li.label || i.label, sub: li.sub || undefined });
    }
    out.push({ ...g, title: lg.title || g.title, items });
  }
  for (const g of nav) if (!out.some((o) => o.key === g.key)) out.push({ ...g, items: [] });
  for (const g of nav) {
    g.items.forEach((i, n) => {
      const id = itemId(i);
      if (used.has(id)) return;
      used.add(id);
      const target = out.find((o) => o.key === g.key)!;
      const prev = n > 0 ? target.items.findIndex((x) => itemId(x) === itemId(g.items[n - 1])) : -1;
      if (n === 0) target.items.unshift({ ...i });
      else if (prev >= 0) target.items.splice(prev + 1, 0, { ...i });
      else target.items.push({ ...i });
    });
  }
  return out;
}

interface DItem { id: string; label: string; def: string; sub: string; icon: LucideIcon; mine: boolean }
interface DGroup { key: string; title: string; def: string; flat?: boolean; icon: LucideIcon; tone: string; items: DItem[] }
type Drag = { kind: "item"; id: string } | { kind: "group"; key: string } | null;
type Over = { kind: "item"; g: string; idx: number } | { kind: "group"; idx: number } | null;

function draftOf(nav: Group[], layout: Layout | null, visible: (i: Item) => boolean): DGroup[] {
  const defItem = new Map<string, string>();
  nav.forEach((g) => g.items.forEach((i) => defItem.set(itemId(i), i.label)));
  return applyLayout(nav, layout).map((g) => ({
    key: g.key, title: g.title, def: nav.find((x) => x.key === g.key)!.title, flat: g.flat, icon: g.icon, tone: g.tone,
    items: g.items.map((i) => ({ id: itemId(i), label: i.label, def: defItem.get(itemId(i)) ?? i.label, sub: i.sub ?? "", icon: i.icon, mine: visible(i) })),
  }));
}

export function MenuEditor({ nav, layout, visible, onDone }: { nav: Group[]; layout: Layout | null; visible: (i: Item) => boolean; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState<DGroup[]>(() => draftOf(nav, layout, visible));
  const [busy, setBusy] = useState(false);
  const [resetAsk, setResetAsk] = useState(false);
  const [drag, setDrag] = useState<Drag>(null);
  const [over, setOver] = useState<Over>(null);
  const [armed, setArmed] = useState("");                 // перетаскивать можно только за ручку, иначе в поле названия нельзя выделить текст
  const live = useRef<HTMLParagraphElement>(null);
  const say = (t: string) => { if (live.current) live.current.textContent = t; };

  const moveItem = (id: string, toKey: string, toIdx: number) => setDraft((d) => {
    const gs = d.map((g) => ({ ...g, items: [...g.items] }));
    let it: DItem | undefined;
    let from = { key: "", idx: -1 };
    for (const g of gs) {
      const k = g.items.findIndex((x) => x.id === id);
      if (k >= 0) { [it] = g.items.splice(k, 1); from = { key: g.key, idx: k }; }
    }
    const tg = gs.find((g) => g.key === toKey);
    if (!it || !tg) return d;
    let at = from.key === toKey && from.idx < toIdx ? toIdx - 1 : toIdx;
    at = Math.max(0, Math.min(at, tg.items.length));
    const above = tg.items[at - 1], below = tg.items[at];
    // пункт попадает в подраздел соседа сверху (или снизу, если он первый); свой подраздел сохраняет, если стоит рядом с ним
    const sub = it.sub && (above?.sub === it.sub || below?.sub === it.sub) ? it.sub : above ? above.sub : below ? below.sub : "";
    tg.items.splice(at, 0, { ...it, sub });
    say(`«${it.label || it.def}»: группа «${tg.title || tg.def}», место ${at + 1}`);
    return gs;
  });
  const moveGroup = (key: string, toIdx: number) => setDraft((d) => {
    const from = d.findIndex((g) => g.key === key);
    if (from < 0) return d;
    const gs = [...d];
    const [g] = gs.splice(from, 1);
    const at = Math.max(0, Math.min(from < toIdx ? toIdx - 1 : toIdx, gs.length));
    gs.splice(at, 0, g);
    return gs;
  });
  const step = (gi: number, n: number, dir: -1 | 1) => {
    const g = draft[gi], it = g.items[n];
    if (dir < 0 && n > 0) moveItem(it.id, g.key, n - 1);
    else if (dir > 0 && n < g.items.length - 1) moveItem(it.id, g.key, n + 2);
    else if (dir < 0 && gi > 0) moveItem(it.id, draft[gi - 1].key, draft[gi - 1].items.length);
    else if (dir > 0 && gi < draft.length - 1) moveItem(it.id, draft[gi + 1].key, 0);
  };
  const setItem = (id: string, label: string) => setDraft((d) => d.map((g) => ({ ...g, items: g.items.map((x) => (x.id === id ? { ...x, label } : x)) })));
  const setTitle = (key: string, title: string) => setDraft((d) => d.map((g) => (g.key === key ? { ...g, title } : g)));
  /** Переименовать подраздел: все пункты подряд с тем же заголовком. */
  const setSub = (key: string, start: number, sub: string) => setDraft((d) => d.map((g) => {
    if (g.key !== key) return g;
    const old = g.items[start].sub;
    const items = [...g.items];
    for (let k = start; k < items.length && items[k].sub === old; k++) items[k] = { ...items[k], sub };
    return { ...g, items };
  }));

  const endDrag = () => { setDrag(null); setOver(null); setArmed(""); };
  const drop = () => {
    if (drag?.kind === "item" && over?.kind === "item") moveItem(drag.id, over.g, over.idx);
    if (drag?.kind === "group" && over?.kind === "group") moveGroup(drag.key, over.idx);
    endDrag();
  };
  const half = (e: React.DragEvent) => { const r = e.currentTarget.getBoundingClientRect(); return e.clientY > r.top + r.height / 2 ? 1 : 0; };

  const save = async () => {
    const clean = (s: string) => s.replace(/\s+/g, " ").trim();
    const body: Layout = {
      groups: draft.map((g) => ({
        key: g.key, title: clean(g.title) === g.def ? "" : clean(g.title),
        items: g.items.map((i) => ({ id: i.id, label: clean(i.label) === i.def ? "" : clean(i.label), sub: clean(i.sub) })),
      })),
    };
    setBusy(true);
    try {
      qc.setQueryData(["menu-layout"], await put<MenuLayoutRes>("/menu-layout", body));
      toast.success("Меню сохранено. Его увидят все");
      onDone();
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    qc.setQueryData(["menu-layout"], await api<MenuLayoutRes>("DELETE", "/menu-layout"));
    toast.success("Меню снова как было по умолчанию");
    onDone();
  };

  return (
    <div className="menu-editor" onDragEnd={endDrag}>
      <div className="me-help">
        <strong>Настройка меню</strong>
        <span className="muted small">Тяните за <GripVertical size={13} aria-hidden /> или двигайте стрелками. Название меняется прямо в поле. Меню одно для всех; кому что видно, решают права.</span>
      </div>
      <p className="sr-only" aria-live="polite" ref={live} />
      {draft.map((g, gi) => {
        const groupOver = over?.kind === "group" && drag?.kind === "group";
        return (
          <section key={g.key} className={`me-group tone-${g.tone} ${drag?.kind === "group" && drag.key === g.key ? "dragging" : ""}
              ${groupOver && over.idx === gi ? "drop-before" : ""} ${groupOver && over.idx === draft.length && gi === draft.length - 1 ? "drop-after" : ""}`}
            draggable={armed === "g:" + g.key}
            onDragStart={(e) => { if (e.target !== e.currentTarget) return; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", g.key); setDrag({ kind: "group", key: g.key }); }}
            onDragOver={(e) => {
              if (drag?.kind !== "group") return;
              e.preventDefault();
              setOver({ kind: "group", idx: gi + half(e) });
            }}
            onDrop={(e) => { e.preventDefault(); drop(); }}>
            <div className="me-head"
              onDragOver={(e) => { if (drag?.kind !== "item") return; e.preventDefault(); e.stopPropagation(); setOver({ kind: "item", g: g.key, idx: 0 }); }}
              onDrop={(e) => { if (drag?.kind !== "item") return; e.preventDefault(); e.stopPropagation(); drop(); }}>
              <span className="me-grip" title="Перетащить группу" onMouseDown={() => setArmed("g:" + g.key)} onMouseUp={() => setArmed("")}><GripVertical size={16} aria-hidden /></span>
              <span className="nav-ico"><g.icon size={15} aria-hidden /></span>
              {g.flat ? <span className="me-flat">Вверху, без заголовка</span> : (
                <input className="me-input me-title" value={g.title} placeholder={g.def} maxLength={40} aria-label={`Название группы «${g.def}»`}
                  onChange={(e) => setTitle(g.key, e.target.value)} />
              )}
              <span className="me-arrows">
                <button type="button" className="me-btn" aria-label={`Группу «${g.title || g.def}» выше`} disabled={gi === 0} onClick={() => moveGroup(g.key, gi - 1)}><ArrowUp size={14} aria-hidden /></button>
                <button type="button" className="me-btn" aria-label={`Группу «${g.title || g.def}» ниже`} disabled={gi === draft.length - 1} onClick={() => moveGroup(g.key, gi + 2)}><ArrowDown size={14} aria-hidden /></button>
              </span>
            </div>
            <ul className={`me-items ${g.items.length === 0 && over?.kind === "item" && over.g === g.key ? "drop-here" : ""}`}
              onDragOver={(e) => { if (drag?.kind !== "item" || g.items.length) return; e.preventDefault(); e.stopPropagation(); setOver({ kind: "item", g: g.key, idx: 0 }); }}
              onDrop={(e) => { if (drag?.kind !== "item") return; e.preventDefault(); e.stopPropagation(); drop(); }}>
              {g.items.length === 0 && <li className="me-empty muted small">Пусто: перетащите сюда пункты</li>}
              {g.items.map((it, n) => {
                const cap = it.sub && it.sub !== g.items[n - 1]?.sub;
                const isOver = over?.kind === "item" && over.g === g.key && drag?.kind === "item";
                return (
                  <li key={it.id}>
                    {cap && (
                      <input className="me-input me-sub" value={it.sub} maxLength={60} aria-label={`Заголовок подраздела «${it.sub}»`}
                        onChange={(e) => setSub(g.key, n, e.target.value)} />
                    )}
                    <div className={`me-item ${it.mine ? "" : "me-other"} ${drag?.kind === "item" && drag.id === it.id ? "dragging" : ""}
                        ${isOver && over.idx === n ? "drop-before" : ""} ${isOver && over.idx === g.items.length && n === g.items.length - 1 ? "drop-after" : ""}`}
                      draggable={armed === it.id}
                      title={it.mine ? undefined : "Вам по правам этот пункт не виден, другим ролям — виден"}
                      onDragStart={(e) => { e.stopPropagation(); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", it.id); setDrag({ kind: "item", id: it.id }); }}
                      onDragOver={(e) => { if (drag?.kind !== "item") return; e.preventDefault(); e.stopPropagation(); setOver({ kind: "item", g: g.key, idx: n + half(e) }); }}
                      onDrop={(e) => { if (drag?.kind !== "item") return; e.preventDefault(); e.stopPropagation(); drop(); }}>
                      <span className="me-grip" title="Перетащить" onMouseDown={() => setArmed(it.id)} onMouseUp={() => setArmed("")}><GripVertical size={15} aria-hidden /></span>
                      <it.icon size={16} aria-hidden className="me-ico" />
                      <input className="me-input" value={it.label} placeholder={it.def} maxLength={60} aria-label={`Название пункта «${it.def}»`}
                        onChange={(e) => setItem(it.id, e.target.value)} />
                      <span className="me-arrows">
                        <button type="button" className="me-btn" aria-label={`«${it.label || it.def}» выше`} disabled={gi === 0 && n === 0} onClick={() => step(gi, n, -1)}><ArrowUp size={14} aria-hidden /></button>
                        <button type="button" className="me-btn" aria-label={`«${it.label || it.def}» ниже`} disabled={gi === draft.length - 1 && n === g.items.length - 1} onClick={() => step(gi, n, 1)}><ArrowDown size={14} aria-hidden /></button>
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
      <div className="me-foot">
        <Button variant="primary" icon={Check} loading={busy} onClick={save}>Сохранить</Button>
        <Button icon={X} onClick={onDone} disabled={busy}>Отмена</Button>
        {layout && <Button variant="ghost" icon={RotateCcw} onClick={() => setResetAsk(true)} disabled={busy}>Вернуть как было</Button>}
      </div>
      {resetAsk && (
        <ConfirmModal title="Вернуть меню как было?" text="Порядок и названия станут такими, как в программе по умолчанию. У всех пользователей." confirmLabel="Вернуть"
          onConfirm={reset} onClose={() => setResetAsk(false)} />
      )}
    </div>
  );
}
