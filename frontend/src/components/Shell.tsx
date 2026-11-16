import { ReactNode, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BellRing, Building2, CalendarCheck, CalendarClock, CalendarDays, ChevronDown, ClipboardList, DatabaseBackup, Gauge, LayoutDashboard, LogOut, Menu, ScrollText, Search, ShieldCheck,
  Truck, User as UserIcon, UserCheck, UserCog, UserSquare2, Users, UsersRound, Wrench, FolderOpen, Fuel, NotebookPen, ShoppingCart, ReceiptText, Megaphone, Newspaper, Boxes, ArrowLeftRight, BarChart3, FileUp, Hourglass, MapPin, Send, type LucideIcon,
} from "lucide-react";
import { ConnectionBar, useOutboxSync } from "./Outbox";
import { BellMenu, useNotifSummary } from "./Bell";
import { ThemeToggle } from "./ThemeToggle";
import { useAuth } from "../auth";
import { get } from "../api";
import { SECTIONS } from "../sections";
import { Empty } from "../ui";

interface Item { to: string; label: string; icon: LucideIcon; any: string[]; end?: boolean; badge?: "pending" | "reminders" | "news"; unless?: string[] }
const sectionItems = (...keys: string[]): Item[] =>
  keys.map((k) => SECTIONS.find((x) => x.key === k)!).map((x) => ({ to: x.path, label: x.label, icon: x.icon, any: [`${x.key}.view`] }));

const NAV: { title: string; items: Item[] }[] = [
  {
    title: "Учет",
    items: [
      { to: "/", label: "Главная", icon: LayoutDashboard, any: ["dashboard.view"], end: true },
      { to: "/vehicles", label: "Транспорт", icon: Truck, any: ["vehicles.view", "vehicles.view_own"] },
      { to: "/requests", label: "Обращения и ремонты", icon: Wrench, any: ["requests.view", "requests.view_own"] },
      { to: "/waybills", label: "Путевые листы", icon: ClipboardList, any: ["waybills.view", "waybills.view_own"] },
      { to: "/readings", label: "Показания на проверке", icon: Gauge, any: ["readings.review"], badge: "pending", end: true },
      { to: "/readings/import", label: "Загрузка показаний", icon: FileUp, any: ["readings.import"] },
      { to: "/calendar", label: "Календарь", icon: CalendarClock, any: ["calendar.view"] },
      { to: "/reminders", label: "Напоминания", icon: BellRing, any: ["reminders.view"], badge: "reminders" },
      { to: "/announcements", label: "Объявления", icon: Megaphone, any: ["announcements.view"], badge: "news" },
      { to: "/news", label: "Мои объявления", icon: Newspaper, any: ["announcements.read_own"], unless: ["announcements.view"], badge: "news" },
    ],
  },
  {
    title: "Персонал",
    items: [
      { to: "/employees", label: "Сотрудники", icon: Users, any: ["employees.view"] },
      { to: "/dossier/employees", label: "Личные дела водителей", icon: UserSquare2, any: ["employee_files.view"] },
      { to: "/timesheet", label: "Табель", icon: CalendarDays, any: ["timesheet.view"] },
      { to: "/my-time", label: "Мои часы", icon: Hourglass, any: ["worklog.view_own", "worklog.create_own"], unless: ["worklog.view"] },
      { to: "/attendance", label: "Выход на работу", icon: UserCheck, any: ["attendance.view", "attendance.mark_own"] },
      ...sectionItems("worklog", "employee_events", "ppe", "ppe_norms"),
    ],
  },
  {
    title: "Техника",
    items: [
      { to: "/roster", label: "Водители и техника", icon: UsersRound, any: ["roster.view"] },
      { to: "/dossier/vehicles", label: "Личные дела техники", icon: FolderOpen, any: ["vehicle_files.view"] },
      ...sectionItems("maintenance", "vehicle_events", "tires", "vehicle_repairs", "vehicle_finance", "fuel_cards", "fines", "rentals"),
    ],
  },
  {
    title: "Ремонт и запчасти",
    items: [
      { to: "/purchases", label: "Что купить", icon: ShoppingCart, any: ["purchases.view"] },
      { to: "/bills", label: "Счета на ремонт", icon: ReceiptText, any: ["bills.view"] },
      ...sectionItems("parts"),
    ],
  },
  {
    title: "Склад",
    items: [
      { to: "/stock", label: "Остатки", icon: Boxes, any: ["stock_items.view"], end: true },
      { to: "/stock/moves", label: "Движение товара", icon: ArrowLeftRight, any: ["stock_moves.view"] },
      ...sectionItems("stock_items", "warehouses"),
    ],
  },
  {
    title: "Задания",
    items: [
      { to: "/jobs", label: "Технические задания", icon: CalendarCheck, any: ["jobs.view"], end: true },
      { to: "/jobs/mine", label: "Мои задания", icon: CalendarCheck, any: ["jobs.view_own"], unless: ["jobs.view"] },
      { to: "/map", label: "Карта", icon: MapPin, any: ["map.view"] },
      ...sectionItems("jobs"),
    ],
  },
  {
    title: "Рейсы и заправки",
    items: [
      { to: "/entries", label: "Мои записи", icon: NotebookPen, any: ["trips.view_own", "fuel_fills.view_own"], unless: ["trips.view", "fuel_fills.view"], end: true },
      { to: "/entries/feed", label: "Записи водителей", icon: Fuel, any: ["trips.view", "fuel_fills.view"] },
      ...sectionItems("trips", "fuel_fills"),
    ],
  },
  { title: "Заказы", items: sectionItems("orders", "documents") },
  { title: "Деньги", items: sectionItems("cash", "cash_accounts", "expenses", "payments") },
  {
    title: "Отчёты",
    items: [{ to: "/reports", label: "Отчёты", icon: BarChart3, any: ["reports.view"] }],
  },
  { title: "Справочники", items: [{ to: "/entities", label: "Юридические лица", icon: Building2, any: ["legal_entities.view"] }, ...sectionItems("counterparties", "tariffs", "constants")] },
  {
    title: "Служебное",
    items: [
      { to: "/users", label: "Пользователи", icon: UserCog, any: ["users.view"] },
      { to: "/roles", label: "Роли и права", icon: ShieldCheck, any: ["users.view"] },
      { to: "/audit", label: "Журнал действий", icon: ScrollText, any: ["audit.view"] },
      { to: "/backups", label: "Резервные копии", icon: DatabaseBackup, any: ["backups.manage"] },
      { to: "/channels", label: "Telegram и почта", icon: Send, any: ["users.view"] },
    ],
  },
];

export function Shell() {
  const { me, can, canAny, entityId, setEntityId, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(false);
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const loc = useLocation();
  const menuRef = useRef<HTMLDivElement>(null);
  useOutboxSync();

  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    const close = (e: MouseEvent) => menuRef.current && !menuRef.current.contains(e.target as Node) && setMenu(false);
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || !menuRef.current?.querySelector(".menu")) return;
      setMenu(false);
      menuRef.current.querySelector<HTMLElement>("button")?.focus();   // фокус возвращается на кнопку меню
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, []);

  const pending = useQuery({
    queryKey: ["pending-count", entityId],
    queryFn: () => get<{ items: unknown[] }>("/readings/pending", { legal_entity_id: entityId ?? undefined }),
    enabled: can("readings.review"),
    refetchInterval: 60_000,
  });

  const due = useQuery({
    queryKey: ["reminders-summary", entityId],
    queryFn: () => get<{ due: number; soon: number; total: number }>("/reminders/summary", { legal_entity_id: entityId ?? undefined }),
    enabled: can("reminders.view"),
    refetchInterval: 5 * 60_000,
  });

  const notif = useNotifSummary();
  const news = notif.data?.news_unread ?? 0;

  const canSearch = canAny("vehicles.view", "vehicles.view_own");
  const roleNames = Array.from(new Set(me!.roles.map((r) => r.name))).join(", ");

  return (
    <div className="app">
      <a className="skip" href="#content">К содержимому</a>
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Главное меню">
        <div className="brand">
          <span className="logo"><Truck size={18} aria-hidden /></span>
          Автопарк
        </div>
        {NAV.map((g) => {
          const items = g.items.filter((i) => canAny(...i.any) && !(i.unless && canAny(...i.unless)));
          if (!items.length) return null;
          return (
            <nav className="nav-group" key={g.title} aria-label={g.title}>
              <div className="nav-title">{g.title}</div>
              {items.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.end} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
                  <i.icon size={18} aria-hidden />
                  {i.label}
                  {i.badge === "pending" && (pending.data?.items.length ?? 0) > 0 && <span className="count">{pending.data!.items.length}</span>}
                  {i.badge === "reminders" && (due.data?.due ?? 0) > 0 && <span className="count" title="Наступило">{due.data!.due}</span>}
                  {i.badge === "news" && news > 0 && <span className="count" title="Адресовано вам, не открыто">{news}</span>}
                </NavLink>
              ))}
            </nav>
          );
        })}
        <div className="foot">Учёт техники, путевые листы, личные дела, деньги и справочники. Остальные разделы старой программы переносятся по очереди.</div>
      </aside>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}

      <div className="main">
        <header className="topbar">
          <button className="btn ghost icon-btn burger" onClick={() => setOpen(true)} aria-label="Открыть меню"><Menu size={20} /></button>
          {canSearch && (
            <form className="search" role="search" onSubmit={(e) => { e.preventDefault(); nav("/vehicles" + (q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "")); }}>
              <Search size={16} aria-hidden />
              <input className="input" placeholder="Найти технику по госномеру или марке" aria-label="Поиск техники" value={q} onChange={(e) => setQ(e.target.value)} />
            </form>
          )}
          <span className="spacer" />
          <ThemeToggle />
          <BellMenu />
          {me!.legal_entities.length > 1 && (
            <select className="input compact" aria-label="Юридическое лицо" value={entityId ?? ""} onChange={(e) => setEntityId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Все юрлица</option>
              {me!.legal_entities.map((e) => <option key={e.id} value={e.id}>{e.short_name}</option>)}
            </select>
          )}
          <div className="user-menu" ref={menuRef}>
            <button className="btn" onClick={() => setMenu((v) => !v)} aria-expanded={menu} aria-haspopup="menu"
              aria-label={`Меню пользователя: ${me!.display_name || me!.login}`}>
              <UserIcon size={16} aria-hidden />
              <span className="hide-sm">{me!.display_name || me!.login}</span>
              <ChevronDown size={14} aria-hidden />
            </button>
            {menu && (
              <div className="menu" role="menu">
                <div className="who">
                  <strong>{me!.display_name || me!.login}</strong>
                  <div className="muted small">{roleNames || "Без роли"}</div>
                </div>
                <NavLink to="/profile" role="menuitem" onClick={() => setMenu(false)}><UserIcon size={16} aria-hidden /> Профиль и пароль</NavLink>
                <button role="menuitem" onClick={async () => { await logout(); nav("/login"); }}><LogOut size={16} aria-hidden /> Выйти</button>
              </div>
            )}
          </div>
        </header>
        <ConnectionBar />
        <main className="content" id="content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/** Закрывает страницу от тех, у кого нет права: прямой адрес в строке браузера тоже не откроет её. */
export function Guard({ any, children }: { any: string[]; children: ReactNode }) {
  const { canAny } = useAuth();
  if (!canAny(...any)) return <Empty icon={ShieldCheck} title="Нет доступа">У вашей роли нет прав на этот раздел. Если это ошибка, обратитесь к администратору.</Empty>;
  return <>{children}</>;
}
