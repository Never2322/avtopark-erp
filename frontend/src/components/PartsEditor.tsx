// Список деталей: название с подсказками из каталога, артикул, количество, единица.
// Используется при создании обращения, при добавлении деталей в карточке и в счёте.
import { useEffect, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { get } from "../api";
import { useDebounced } from "../hooks";
import { Button } from "../ui";

export interface PartRow { name: string; article: string; qty: string; unit: string; price?: string }
export const PART_UNITS = ["шт", "комплект", "л", "кг", "м", "пара"];
export const BILL_UNITS = [...PART_UNITS, "ч", "усл."];

export const emptyPart = (withPrice = false): PartRow => (withPrice ? { name: "", article: "", qty: "1", unit: "шт", price: "" } : { name: "", article: "", qty: "1", unit: "шт" });
/** Строки, в которых что-то введено: пустая строка «про запас» на сервер не уходит. */
export const filledParts = (rows: PartRow[]) => rows.filter((r) => r.name.trim() || r.article.trim());

interface Option { id: number; name: string; article: string; unit: string; category: string; uses: number }

export function usePartOptions(text: string, enabled = true) {
  const q = useDebounced(text.trim(), 250);
  return useQuery({
    queryKey: ["part-options", q],
    queryFn: () => get<{ items: Option[] }>("/part-options", { q, limit: 12 }),
    enabled,
    staleTime: 60_000,
    retry: false,
    placeholderData: (p) => p,
  });
}

export function PartsEditor({ rows, onChange, units = PART_UNITS, errors, withPrice = false, max = 60, hint }: {
  rows: PartRow[]; onChange: (rows: PartRow[]) => void; units?: string[]; errors?: string; withPrice?: boolean; max?: number; hint?: string;
}) {
  const listId = useId();
  const [focus, setFocus] = useState<number | null>(null);
  const text = focus !== null ? rows[focus]?.name ?? "" : "";
  const opts = usePartOptions(text, focus !== null);
  useEffect(() => { if (rows.length === 0) onChange([emptyPart(withPrice)]); }, [rows.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (i: number, patch: Partial<PartRow>) => onChange(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const setName = (i: number, name: string) => {
    const hit = opts.data?.items.find((o) => o.name.toLowerCase() === name.trim().toLowerCase());
    const cur = rows[i];
    // выбрали деталь из подсказки: артикул и единицу подставляем, только если человек их ещё не менял
    set(i, hit ? { name: hit.name, article: cur.article || hit.article, unit: cur.unit === "шт" && units.includes(hit.unit) ? hit.unit : cur.unit } : { name });
  };

  return (
    <div className="parts-editor">
      <datalist id={listId}>
        {opts.data?.items.map((o) => <option key={o.id} value={o.name}>{o.article ? `${o.article} · ` : ""}{o.category}</option>)}
      </datalist>
      <div className="parts-rows">
        {rows.map((r, i) => (
          <div className={`parts-row ${withPrice ? "with-price" : ""}`} key={i}>
            <input className="input p-name" list={listId} aria-label={`Название детали, строка ${i + 1}`} placeholder="Название, например: Фильтр масляный" maxLength={200}
              value={r.name} onFocus={() => setFocus(i)} onChange={(e) => setName(i, e.target.value)} autoComplete="off" />
            <input className="input p-article" aria-label={`Артикул, строка ${i + 1}`} placeholder="Артикул" maxLength={60} value={r.article} onChange={(e) => set(i, { article: e.target.value })} />
            <input className="input p-qty" inputMode="decimal" aria-label={`Количество, строка ${i + 1}`} placeholder="Кол-во" value={r.qty} onChange={(e) => set(i, { qty: e.target.value })} />
            <select className="input p-unit" aria-label={`Единица, строка ${i + 1}`} value={r.unit} onChange={(e) => set(i, { unit: e.target.value })}>
              {units.map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
            {withPrice && <input className="input p-price" inputMode="decimal" aria-label={`Цена с НДС, строка ${i + 1}`} placeholder="Цена, ₽" value={r.price ?? ""} onChange={(e) => set(i, { price: e.target.value })} />}
            <Button variant="ghost" size="sm" icon={Trash2} aria-label={`Убрать строку ${i + 1}`} onClick={() => onChange(rows.length > 1 ? rows.filter((_, k) => k !== i) : [emptyPart(withPrice)])} />
          </div>
        ))}
      </div>
      {errors && <span className="err" role="alert">{errors}</span>}
      {!errors && hint && <span className="hint">{hint}</span>}
      <div style={{ marginTop: 8 }}>
        <Button size="sm" icon={Plus} disabled={rows.length >= max} onClick={() => onChange([...rows, emptyPart(withPrice)])}>Ещё строка</Button>
      </div>
    </div>
  );
}
