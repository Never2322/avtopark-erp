import { Moon, Sun } from "lucide-react";
import { useTheme } from "../theme";

/** Кнопка «солнце/луна»: переключает светлую и тёмную тему. */
export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  const text = theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему";
  return (
    <button type="button" className="btn ghost icon-btn theme-toggle" onClick={toggle} aria-label={text} title={text}>
      {theme === "dark" ? <Sun size={18} aria-hidden /> : <Moon size={18} aria-hidden />}
    </button>
  );
}
