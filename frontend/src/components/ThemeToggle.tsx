import { useEffect, useRef, useState } from "react";
import { Check, Flame, Moon, Sun } from "lucide-react";
import { THEMES, useTheme, type Theme } from "../theme";

const ICON = { dark: Moon, light: Sun, crimson: Flame } as const;

/** Выбор темы оформления: чёрная, светлая или багровая. */
export function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); ref.current?.querySelector("button")?.focus(); } };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const Icon = ICON[theme];
  const pick = (t: Theme) => { setTheme(t); setOpen(false); };
  return (
    <div className="theme-pick" ref={ref}>
      <button type="button" className="btn ghost icon-btn theme-toggle" onClick={() => setOpen((v) => !v)} aria-haspopup="menu" aria-expanded={open}
        aria-label={`Тема оформления: ${THEMES.find((x) => x.id === theme)!.label}. Выбрать другую`} title="Тема оформления">
        <Icon size={18} aria-hidden />
      </button>
      {open && (
        <div className="menu theme-menu" role="menu" aria-label="Тема оформления">
          {THEMES.map((t) => {
            const I = ICON[t.id];
            return (
              <button key={t.id} type="button" role="menuitemradio" aria-checked={theme === t.id} onClick={() => pick(t.id)}>
                <span className={`theme-dot theme-dot-${t.id}`} aria-hidden><I size={13} /></span> {t.label}
                {theme === t.id && <Check size={15} aria-hidden className="theme-check" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
