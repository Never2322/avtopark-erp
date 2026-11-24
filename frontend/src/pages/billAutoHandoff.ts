// Передача из окна «Загрузить счёт» в форму счёта: то, что программа прочитала, и файлы, которые человек уже приложил.
// Лежит в памяти страницы (не в адресе): файлы в адрес не положить. Форма забирает данные при открытии и очищает.

export interface AutoResult {
  created: boolean;
  bill: { id: number; number: string; total: number; supplier_name: string; bill_date: string } | null;
  duplicate: { id: number; number: string } | null;
  found: Record<string, any>;
  missing: string[];
  how: "pdf" | "scan" | "photo" | "none";
  readable: boolean;
  confirmed: boolean;
  note: string;
  errors: Record<string, string>;
}

export interface AutoHandoff {
  found: Record<string, any>;
  files: File[];
  note: string;
  missing: string[];
  errors: Record<string, string>;
  message: string;
  legalEntityId: string;
}

let pending: AutoHandoff | null = null;

export const giveHandoff = (h: AutoHandoff) => { pending = h; };
export const peekHandoff = () => pending;
export const clearHandoff = () => { pending = null; };

/** Названия полей счёта для текста «не удалось прочитать: …». */
export const FIELD_LABELS: Record<string, string> = {
  number: "номер счёта", bill_date: "дата счёта", total: "сумма", supplier_name: "поставщик", legal_entity_id: "юридическое лицо",
};
