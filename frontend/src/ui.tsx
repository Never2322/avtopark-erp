import { ButtonHTMLAttributes, createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  AlertTriangle, ArrowDown, ArrowUp, CheckCircle2, ChevronLeft, ChevronRight, Flame, Hourglass, Inbox, Info, Loader2,
  Lock, Minus, Package, Play, Search, Wrench, X, XCircle, FileText, type LucideIcon,
} from "lucide-react";
import { ApiError, errorText } from "./api";

/* ---------------- кнопки и статусы ---------------- */

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "primary" | "danger" | "ghost";
  size?: "md" | "sm" | "lg";
  icon?: LucideIcon;
  loading?: boolean;
}

export function Button({ variant = "default", size = "md", icon: Icon, loading, children, className = "", disabled, type = "button", ...rest }: BtnProps) {
  const cls = ["btn", variant !== "default" ? variant : "", size === "sm" ? "sm" : size === "lg" ? "lg" : "", className].filter(Boolean).join(" ");
  return (
    <button type={type} className={cls} disabled={disabled || loading} {...rest}>
      {loading ? <Loader2 size={16} className="spin" aria-hidden /> : Icon ? <Icon size={16} aria-hidden /> : null}
      {children}
    </button>
  );
}

export type Tone = "success" | "warning" | "error" | "info" | "mint" | "neutral";

export function Badge({ tone = "neutral", icon: Icon, children }: { tone?: Tone; icon?: LucideIcon; children: ReactNode }) {
  return (
    <span className={`badge ${tone}`}>
      {Icon && <Icon size={13} aria-hidden />}
      {children}
    </span>
  );
}

const VEHICLE: Record<string, [Tone, LucideIcon]> = {
  in_work: ["success", CheckCircle2], in_repair: ["error", Wrench],
};
const REQUEST: Record<string, [Tone, LucideIcon]> = {
  new: ["info", Inbox], diagnostics: ["info", Search], approval: ["warning", Hourglass], in_progress: ["mint", Wrench],
  waiting_parts: ["warning", Package], done: ["success", CheckCircle2], closed: ["neutral", Lock], cancelled: ["neutral", XCircle],
};
const WAYBILL: Record<string, [Tone, LucideIcon]> = {
  draft: ["neutral", FileText], open: ["mint", Play], closed: ["success", CheckCircle2], cancelled: ["neutral", XCircle],
};
const PRIORITY: Record<string, [Tone, LucideIcon]> = {
  low: ["neutral", ArrowDown], normal: ["info", Minus], high: ["warning", ArrowUp], urgent: ["error", Flame],
};

function mapBadge(table: Record<string, [Tone, LucideIcon]>, code: string, label: string) {
  const [tone, icon] = table[code] ?? ["neutral", Info];
  return <Badge tone={tone} icon={icon}>{label}</Badge>;
}
export const VehicleStatus = ({ code, label }: { code: string; label: string }) => mapBadge(VEHICLE, code, label);
export const RequestStatus = ({ code, label }: { code: string; label: string }) => mapBadge(REQUEST, code, label);
export const WaybillStatus = ({ code, label }: { code: string; label: string }) => mapBadge(WAYBILL, code, label);
export const Priority = ({ code, label }: { code: string; label: string }) => mapBadge(PRIORITY, code, label);
export const statusTone = (kind: "vehicle" | "request" | "waybill", code: string): Tone => ({ vehicle: VEHICLE, request: REQUEST, waybill: WAYBILL }[kind][code]?.[0] ?? "neutral");

/* ---------------- состояния ---------------- */

export function Alert({ tone = "info", children }: { tone?: "info" | "error" | "warning" | "success"; children: ReactNode }) {
  const Icon = tone === "error" || tone === "warning" ? AlertTriangle : tone === "success" ? CheckCircle2 : Info;
  return (
    <div className={`alert ${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon size={18} aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function Spinner({ label = "Загрузка…" }: { label?: string }) {
  return (
    <div className="empty" role="status">
      <Loader2 className="spin" size={26} aria-hidden />
      <span>{label}</span>
    </div>
  );
}

export function Empty({ icon: Icon = Inbox, title, children }: { icon?: LucideIcon; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon size={34} aria-hidden />
      <strong style={{ color: "var(--text)" }}>{title}</strong>
      {children && <div>{children}</div>}
    </div>
  );
}

export function NotFound({ what = "Страница", text }: { what?: string; text?: ReactNode }) {
  return (
    <div className="empty">
      <Search size={34} aria-hidden />
      <strong style={{ color: "var(--text)" }}>{what} не найден{what.endsWith("а") ? "а" : what.endsWith("е") ? "о" : ""}</strong>
      <div>{text ?? "Возможно, ссылка устарела или у вас нет доступа к этим данным."}</div>
    </div>
  );
}

export function QueryState({ q, children, what }: { q: { isLoading: boolean; error: unknown; refetch: () => void }; children: ReactNode; what?: string }) {
  if (q.isLoading) return <Spinner />;
  if (q.error instanceof ApiError && q.error.status === 404) return <NotFound what={what ?? "Запись"} text={q.error.message.toLowerCase().startsWith((what ?? "Запись").toLowerCase()) ? undefined : q.error.message} />;
  if (q.error)
    return (
      <div className="empty">
        <AlertTriangle size={34} color="var(--error)" aria-hidden />
        <strong style={{ color: "var(--text)" }}>Не удалось загрузить</strong>
        <div>{errorText(q.error)}</div>
        <Button onClick={() => q.refetch()}>Повторить</Button>
      </div>
    );
  return <>{children}</>;
}

/* ---------------- страница, карточка, вкладки ---------------- */

export function PageHeader({ title, sub, actions, crumb }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; crumb?: ReactNode }) {
  return (
    <div className="page-head">
      <div className="titles">
        {crumb && <div className="crumb">{crumb}</div>}
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      {actions && <div className="row wrap">{actions}</div>}
    </div>
  );
}

export function Card({ title, icon: Icon, actions, children, flush, className = "" }: { title?: ReactNode; icon?: LucideIcon; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string }) {
  return (
    <section className={`card ${flush ? "flush" : ""} ${className}`}>
      {title && (
        <div className="card-head" style={flush ? { padding: "16px 18px 0" } : undefined}>
          <h2>{Icon && <Icon size={18} aria-hidden className="card-icon" />}{title}</h2>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  const box = useRef<HTMLDivElement>(null);
  // выбранная вкладка может оказаться за краем экрана (ссылка с ?tab=…): подвигаем полосу, а не страницу
  useEffect(() => {
    const el = box.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    const bar = box.current;
    if (!el || !bar) return;
    const l = el.offsetLeft, r = l + el.offsetWidth;
    if (l < bar.scrollLeft) bar.scrollLeft = l - 8;
    else if (r > bar.scrollLeft + bar.clientWidth) bar.scrollLeft = r - bar.clientWidth + 8;
  }, [value]);
  return (
    <div className="tabs" role="tablist" ref={box}>
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className="tab" onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function FilterChips<T extends string>({ options, value, onChange }: { options: { id: T; label: string; count?: number }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="chips" role="group">
      {options.map((o) => (
        <button key={o.id} className="chip-btn" aria-pressed={value === o.id} onClick={() => onChange(o.id)}>
          {o.label}
          {o.count !== undefined && <span className="n">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ---------------- таблица ---------------- */

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  num?: boolean;
  sortKey?: string;
  hideSm?: boolean;
  width?: number | string;
}

export function Table<T>({ columns, rows, rowKey, onRowClick, sort, onSort, dim }: {
  columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string | number; onRowClick?: (r: T) => void;
  sort?: { key: string; desc: boolean }; onSort?: (key: string) => void; dim?: (r: T) => boolean;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {columns.map((c) => {
              const sortable = !!(c.sortKey && onSort);
              const active = sort && c.sortKey === sort.key;
              return (
                <th
                  key={c.key}
                  className={`${c.num ? "num" : ""} ${sortable ? "sortable" : ""} ${c.hideSm ? "hide-sm" : ""}`}
                  style={c.width ? { width: c.width } : undefined}
                  aria-sort={active ? (sort!.desc ? "descending" : "ascending") : undefined}
                  onClick={sortable ? () => onSort!(c.sortKey!) : undefined}
                >
                  {c.header}
                  {active ? (sort!.desc ? " ↓" : " ↑") : ""}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={rowKey(r)}
              className={`${onRowClick ? "clickable" : ""} ${dim?.(r) ? "dim" : ""}`}
              onClick={onRowClick ? () => onRowClick(r) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={onRowClick ? (e) => e.key === "Enter" && onRowClick(r) : undefined}
            >
              {columns.map((c) => (
                <td key={c.key} className={`${c.num ? "num" : ""} ${c.hideSm ? "hide-sm" : ""}`}>
                  {c.render(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="table-foot">
      <span>{total === 0 ? "Нет записей" : `${from}–${to} из ${total}`}</span>
      <span className="spacer" />
      {pages > 1 && (
        <>
          <Button size="sm" icon={ChevronLeft} disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Предыдущая страница" />
          <span>
            {page} / {pages}
          </span>
          <Button size="sm" icon={ChevronRight} disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Следующая страница" />
        </>
      )}
    </div>
  );
}

/* ---------------- формы ---------------- */

export function Field({ label, error, hint, children, full }: { label: ReactNode; error?: string; hint?: ReactNode; children: ReactNode; full?: boolean }) {
  return (
    <div className={`field ${full ? "full" : ""}`}>
      <label>
        <span className="label">{label}</span>
        {children}
      </label>
      {error ? <span className="err" role="alert">{error}</span> : hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

export function useForm<T extends Record<string, any>>(initial: T) {
  const [values, setValues] = useState<T>(initial);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const set = useCallback(<K extends keyof T>(key: K, value: T[K]) => {
    setValues((s) => ({ ...s, [key]: value }));
    setErrors((e) => {
      if (!(key as string in e)) return e;
      const { [key as string]: _drop, ...rest } = e;
      return rest;
    });
  }, []);
  const fail = useCallback((err: unknown) => {
    if (err instanceof ApiError) {
      if (err.fields) setErrors(err.fields);
      else if (err.field) setErrors({ [err.field]: err.message });
      setFormError(err.message);
    } else setFormError(errorText(err));
  }, []);
  const clear = useCallback(() => {
    setErrors({});
    setFormError("");
  }, []);
  return { values, set, errors, formError, fail, clear, setValues };
}

/** Число из поля ввода. Пусто — null. Не число — ошибка у поля: иначе «12abc» молча превратилось бы в пустое значение. */
export const numOrNull = (s: string, field?: string): number | null => {
  if (s.trim() === "") return null;
  const n = Number(s.trim().replace(/\s/g, "").replace(",", "."));
  if (!Number.isFinite(n)) throw new ApiError(422, "bad_number", "Введите число, например 12,5", field);
  return n;
};

/* ---------------- окна ---------------- */

export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const down = useRef(false);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("input:not([disabled]), select, textarea, button.primary");
    first?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") return onClose();
      if (e.key !== "Tab" || !ref.current) return;
      // Tab ходит по кругу внутри верхнего окна и не уходит на страницу за ним
      const all = document.querySelectorAll(".modal");
      if (all[all.length - 1] !== ref.current) return;
      const items = Array.from(ref.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
        .filter((el) => el.getClientRects().length > 0);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1], now = document.activeElement as HTMLElement | null;
      if (!now || !ref.current.contains(now)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && now === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && now === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", key);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", key);
      document.body.style.overflow = "";
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => (down.current = e.target === e.currentTarget)} onClick={(e) => down.current && e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <div className="modal-head">
          <h2>{title}</h2>
          <Button variant="ghost" size="sm" icon={X} onClick={onClose} aria-label="Закрыть" />
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/** Окно «подтвердите действие», при необходимости с обязательной причиной. */
export function ConfirmModal({ title, text, confirmLabel, danger, reasonLabel, reasonRequired = true, onConfirm, onClose }: {
  title: string; text?: ReactNode; confirmLabel: string; danger?: boolean; reasonLabel?: string; reasonRequired?: boolean;
  onConfirm: (reason: string) => Promise<unknown>; onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async () => {
    if (reasonLabel && reasonRequired && reason.trim().length < 3) return setError("Укажите причину (не короче 3 символов)");
    setBusy(true);
    setError("");
    try {
      await onConfirm(reason.trim());
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant={danger ? "danger" : "primary"} loading={busy} onClick={submit}>{confirmLabel}</Button>
    </>}>
      <div className="stack-sm">
        {text && <div>{text}</div>}
        {reasonLabel && (
          <Field label={reasonLabel} error={error && reason.trim().length < 3 ? error : undefined}>
            <textarea className="input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />
          </Field>
        )}
        {error && !(reasonLabel && reason.trim().length < 3) && <Alert tone="error">{error}</Alert>}
      </div>
    </Modal>
  );
}

/* ---------------- уведомления ---------------- */

interface Toast { id: number; tone: "success" | "error"; text: string }
const ToastCtx = createContext<{ success: (t: string) => void; error: (t: string) => void }>({ success: () => {}, error: () => {} });
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s, { id, tone, text }]);
    setTimeout(() => setItems((s) => s.filter((t) => t.id !== id)), Math.max(tone === "error" ? 7000 : 4000, text.length * 70));
  }, []);
  const api = { success: (t: string) => push("success", t), error: (t: string) => push("error", t) };
  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`} role={t.tone === "error" ? "alert" : "status"}>
            {t.tone === "success" ? <CheckCircle2 size={18} color="var(--success)" aria-hidden /> : <AlertTriangle size={18} color="var(--error)" aria-hidden />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
