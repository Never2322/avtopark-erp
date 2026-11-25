import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BellRing, Building2, Camera, CalendarCheck, CalendarClock, CalendarDays, ChevronDown, ChevronRight, ClipboardList, DatabaseBackup, Gauge, LayoutDashboard, LogOut, Menu, ScrollText, Search, ShieldCheck,
  Truck, User as UserIcon, UserCheck, UserCog, UserSquare2, Users, UsersRound, Wrench, FolderOpen, Fuel, NotebookPen, ShoppingCart, ReceiptText, Receipt, Megaphone, Newspaper, Boxes, ArrowLeftRight, BarChart3, FileUp, Hourglass, ListOrdered, MapPin, Recycle, Signpost, CalendarRange, Send, Settings, Wallet, Weight, X, type LucideIcon,
} from "lucide-react";
import { ConnectionBar, useOutboxSync } from "./Outbox";
import { BellMenu, useNotifSummary } from "./Bell";
import { ThemeToggle } from "./ThemeToggle";
import { useAuth } from "../auth";
import { get } from "../api";
import { SECTIONS } from "../sections";
import { Empty } from "../ui";

/** sub: заголовок подраздела внутри группы (есть у каждого пункта подраздела, показывается над первым видимым: если первый скрыт правами, заголовок не пропадает); words: как ещё люди называют этот раздел (для поиска). */
interface Item { to: string; label: string; icon: LucideIcon; any: string[]; end?: boolean; badge?: "pending" | "reminders" | "news"; unless?: string[]; all?: string[]; feature?: "tonnage" | "hauls" | "routes"; sub?: string; words?: string }
interface Group { key: string; title: string; icon: LucideIcon; tone: string; items: Item[]; flat?: boolean }

const sectionItems = (...keys: string[]): Item[] =>
  keys.map((k) => SECTIONS.find((x) => x.key === k)!).map((x) => ({ to: x.path, label: x.label, icon: x.icon, any: [`${x.key}.view`] }));
/** Первому пункту списка добавляет заголовок подраздела. */
const withSub = (sub: string, items: Item[]): Item[] => items.map((i) => ({ ...i, sub }));

// Семь понятных групп вместо тринадцати: что делаем сегодня, мусоровозы, техника, ремонт и склад, люди, деньги, справочники.
const NAV: Group[] = [
  {
    key: "home", title: "Главное", icon: LayoutDashboard, tone: "mint", flat: true,
    items: [
      { to: "/", label: "Главная", icon: LayoutDashboard, any: ["dashboard.view"], end: true, words: "сводка начало" },
      { to: "/", label: "Мой мусоровоз", icon: Recycle, any: ["loaders.view_own"], unless: ["dashboard.view"], end: true, words: "грузчик машина маршрут замечание" },
      { to: "/calendar", label: "Календарь", icon: CalendarClock, any: ["calendar.view"], words: "даты сроки события" },
      { to: "/reminders", label: "Напоминания", icon: BellRing, any: ["reminders.view"], badge: "reminders", words: "сроки то просрочено" },
      { to: "/announcements", label: "Объявления", icon: Megaphone, any: ["announcements.view"], badge: "news", words: "новости сообщения" },
      { to: "/news", label: "Мои объявления", icon: Newspaper, any: ["announcements.read_own"], unless: ["announcements.view"], badge: "news", words: "новости сообщения" },
    ],
  },
  {
    key: "work", title: "Работа", icon: CalendarCheck, tone: "info",
    items: [
      ...withSub("Задания", [
        { to: "/jobs", label: "Технические задания", icon: CalendarCheck, any: ["jobs.view"], end: true, words: "наряд заявка на день" },
        { to: "/jobs/mine", label: "Мои задания", icon: CalendarCheck, any: ["jobs.view_own"], unless: ["jobs.view"], end: true, words: "наряд на сегодня" },
        { to: "/map", label: "Карта", icon: MapPin, any: ["map.view"], words: "где машины объекты" },
        ...sectionItems("jobs"),
      ]),
      ...withSub("Путевые листы и показания", [
        { to: "/waybills", label: "Путевые листы", icon: ClipboardList, any: ["waybills.view", "waybills.view_own"] },
        { to: "/readings", label: "Показания на проверке", icon: Gauge, any: ["readings.review"], badge: "pending", end: true, words: "пробег моточасы одометр" },
        { to: "/readings/import", label: "Ввод показаний", icon: FileUp, any: ["readings.import", "readings.create", "readings.create_own"], words: "спидометр пробег моточасы вручную файл загрузка показаний" },
      ]),
      ...withSub("Рейсы и заправки", [
        { to: "/entries", label: "Мои записи", icon: NotebookPen, any: ["trips.view_own", "fuel_fills.view_own"], unless: ["trips.view", "fuel_fills.view"], end: true, words: "рейс заправка внести" },
        { to: "/entries/feed", label: "Записи водителей", icon: Fuel, any: ["trips.view", "fuel_fills.view"], words: "рейсы заправки лента" },
        ...sectionItems("trips", "fuel_fills"),
      ]),
    ],
  },
  {
    key: "garbage", title: "Мусоровозы", icon: Recycle, tone: "success",
    items: [
      ...withSub("Маршруты", [
        { to: "/my-routes", label: "Мои маршруты", icon: Signpost, any: ["route_plan.view_own"], unless: ["route_plan.view"], feature: "routes", end: true, words: "мусоровоз улицы объезд по дням" },
        { to: "/route-board", label: "Маршруты по дням", icon: CalendarRange, any: ["route_plan.view"], end: true, words: "какой мусоровоз где сегодня работает улицы" },
        { to: "/loaders", label: "Бригады и замечания", icon: UsersRound, any: ["route_notes.view"], end: true, words: "грузчики бригада экипаж замечания не вывезли контейнер" },
        ...sectionItems("route_plan", "garbage_routes"),
      ]),
      ...withSub("Тоннаж и ходки", [
        { to: "/tonnage", label: "Тоннаж", icon: Weight, any: ["weigh_tickets.view_own"], unless: ["weigh_tickets.view"], feature: "tonnage", end: true, words: "талон вес весы тонны" },
        { to: "/hauls", label: "Счётчик ходок", icon: ListOrdered, any: ["hauls.use_own"], feature: "hauls", end: true, words: "ходки рейсы" },
        ...sectionItems("weigh_tickets"),
        { to: "/reports/tonnage", label: "Тоннаж за месяц", icon: BarChart3, any: ["weigh_tickets.view"], all: ["reports.view"], words: "тонны итог отчёт" },
      ]),
      ...withSub("Зарплата", [
        { to: "/reports/garbage_salary", label: "Зарплата мусоровозов", icon: Wallet, any: ["worklog.view"], all: ["reports.view"], words: "зарплата заработок выплата тонны водители мусоровозов аванс" },
      ]),
    ],
  },
  {
    key: "fleet", title: "Техника", icon: Truck, tone: "mint",
    items: [
      { to: "/vehicles", label: "Транспорт", icon: Truck, any: ["vehicles.view", "vehicles.view_own"], words: "машины автомобили парк спецтехника" },
      { to: "/roster", label: "Водители и техника", icon: UsersRound, any: ["roster.view"], words: "закрепление кто на чём" },
      { to: "/dossier/vehicles", label: "Личные дела техники", icon: FolderOpen, any: ["vehicle_files.view"], words: "документы птс страховка осаго" },
      ...withSub("Обслуживание", sectionItems("maintenance", "vehicle_events", "tires", "vehicle_repairs")),
      ...withSub("Деньги по технике", sectionItems("vehicle_finance", "fines", "fuel_cards", "rentals")),
    ],
  },
  {
    key: "repair", title: "Ремонт и склад", icon: Wrench, tone: "warning",
    items: [
      ...withSub("Ремонт", [
        { to: "/requests", label: "Обращения и ремонты", icon: Wrench, any: ["requests.view", "requests.view_own"], words: "поломка заявка на ремонт" },
        { to: "/repair-photos", label: "Фото ремонта", icon: Camera, any: ["repair_photos.view"], words: "после ремонта результат отфотать подтверждено что отремонтировали история" },
        { to: "/purchases", label: "Что купить", icon: ShoppingCart, any: ["purchases.view"], words: "закупка запчасти список" },
        ...sectionItems("parts"),
      ]),
      ...withSub("Склад", [
        { to: "/stock", label: "Остатки", icon: Boxes, any: ["stock_items.view"], end: true, words: "склад сколько осталось" },
        { to: "/stock/moves", label: "Движение товара", icon: ArrowLeftRight, any: ["stock_moves.view"], words: "приход расход списание" },
        ...sectionItems("stock_items", "warehouses"),
      ]),
    ],
  },
  {
    key: "people", title: "Сотрудники", icon: Users, tone: "success",
    items: [
      { to: "/employees", label: "Сотрудники", icon: Users, any: ["employees.view"], words: "люди водители работники" },
      { to: "/dossier/employees", label: "Личные дела водителей", icon: UserSquare2, any: ["employee_files.view"], words: "документы права медосмотр" },
      { to: "/timesheet", label: "Табель", icon: CalendarDays, any: ["timesheet.view"], words: "часы зарплата выходы учёт времени" },
      { to: "/my-time", label: "Мои часы", icon: Hourglass, any: ["worklog.view_own"], unless: ["worklog.view"], words: "заработок зарплата" },
      { to: "/hours-entry", label: "Внести часы", icon: Hourglass, any: ["worklog.enter"], unless: ["worklog.create"], words: "ремонт механик" },
      { to: "/attendance", label: "Выход на работу", icon: UserCheck, any: ["attendance.view", "attendance.mark_own"], words: "отметка явка смена" },
      ...sectionItems("worklog", "employee_events"),
      ...withSub("Спецодежда", sectionItems("ppe", "ppe_norms")),
    ],
  },
  {
    key: "money", title: "Деньги и заказы", icon: Wallet, tone: "violet",
    items: [
      { to: "/expenses", label: "Расходы", icon: Receipt, any: ["reports.money"], end: true, words: "траты затраты счета итог месяц" },
      { to: "/bills", label: "Счета на ремонт", icon: ReceiptText, any: ["bills.view"], words: "запчасти поставщик оплата ремонт счёт к оплате" },
      ...sectionItems("expenses", "orders", "documents"),
      ...withSub("Касса", sectionItems("cash", "cash_accounts", "payments")),
      ...withSub("Отчёты", [{ to: "/reports", label: "Отчёты", icon: BarChart3, any: ["reports.view"], words: "затраты зарплата выручка" }]),
    ],
  },
  {
    key: "setup", title: "Настройки", icon: Settings, tone: "neutral",
    items: [
      ...withSub("Справочники", [{ to: "/entities", label: "Юридические лица", icon: Building2, any: ["legal_entities.view"], words: "ооо ип организации" }, ...sectionItems("counterparties", "tariffs", "constants")]),
      ...withSub("Система", [
        { to: "/users", label: "Пользователи", icon: UserCog, any: ["users.view"], words: "логины доступ" },
        { to: "/roles", label: "Роли и права", icon: ShieldCheck, any: ["users.view"], words: "доступ разрешения" },
        { to: "/audit", label: "Журнал действий", icon: ScrollText, any: ["audit.view"], words: "история кто что менял" },
        { to: "/backups", label: "Резервные копии", icon: DatabaseBackup, any: ["backups.manage"], words: "бэкап сохранить данные" },
        { to: "/channels", label: "Telegram и почта", icon: Send, any: ["users.view"], words: "уведомления бот" },
      ]),
    ],
  },
];

/** Страницы без пункта в меню: подпись «где я» всё равно должна быть. */
const EXTRA_TITLES: Record<string, string> = { "/profile": "Профиль и пароль", "/notifications": "Уведомления", "/outbox": "Очередь отправки" };

const matches = (i: Item, path: string) => (i.end ? path === i.to : path === i.to || path.startsWith(i.to + "/"));

export function Shell() {
  const { me, can, canAny, entityId, setEntityId, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(false);
  const [quick, setQuick] = useState(false);
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
  // Ctrl+K (или Cmd+K) открывает быстрый поиск раздела с любой страницы
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === "k" || e.key === "K" || e.key === "л" || e.key === "Л")) { e.preventDefault(); setQuick(true); }
    };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
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
  const badgeOf = (b?: Item["badge"]) => (b === "pending" ? pending.data?.items.length ?? 0 : b === "reminders" ? due.data?.due ?? 0 : b === "news" ? news : 0);

  const canSearch = canAny("vehicles.view", "vehicles.view_own");
  const roleNames = Array.from(new Set(me!.roles.map((r) => r.name))).join(", ");

  // что доступно этому человеку; пустые группы не показываются
  const groups = NAV.map((g) => ({
    ...g,
    items: g.items.filter((i) => canAny(...i.any) && !(i.unless && canAny(...i.unless)) && (!i.all || i.all.every((k) => can(k))) && (!i.feature || me!.features?.[i.feature])),
  })).filter((g) => g.items.length > 0);

  // где человек сейчас: самый подходящий пункт меню (самый длинный адрес) и его группа
  let here: { group: Group; item: Item } | null = null;
  for (const g of groups) for (const i of g.items) if (matches(i, loc.pathname) && (!here || i.to.length > here.item.to.length)) here = { group: g, item: i };
  const hereKey = here?.group.key ?? "";
  const hereTitle = here?.item.label ?? EXTRA_TITLES[loc.pathname] ?? "";

  // раскрыта одна группа: та, где человек сейчас; остальные можно открыть вручную
  const [openKey, setOpenKey] = useState<string>(hereKey);
  useEffect(() => { if (hereKey) setOpenKey(hereKey); }, [hereKey]);

  // название раздела в заголовке вкладки браузера
  useEffect(() => { document.title = hereTitle ? `${hereTitle} · Автопарк ERP` : "Автопарк ERP"; }, [hereTitle]);

  return (
    <div className="app">
      <a className="skip" href="#content">К содержимому</a>
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Главное меню">
        <div className="brand">
          <span className="logo"><Truck size={18} aria-hidden /></span>
          Автопарк
          <button type="button" className="btn ghost icon-btn drawer-close" onClick={() => setOpen(false)} aria-label="Закрыть меню"><X size={18} aria-hidden /></button>
        </div>
        <button type="button" className="nav-search" onClick={() => { setOpen(false); setQuick(true); }} aria-haspopup="dialog" title="Найти раздел по названию (Ctrl+K)">
          <Search size={16} aria-hidden />
          <span>Найти раздел</span>
          <kbd aria-hidden>Ctrl K</kbd>
        </button>
        {groups.map((g) => {
          const expanded = g.flat || openKey === g.key;
          const hasHere = hereKey === g.key;
          const sum = g.items.reduce((n, i) => n + badgeOf(i.badge), 0);
          let lastSub = "";
          return (
            <nav className={`nav-group tone-${g.tone} ${g.flat ? "flat" : ""} ${expanded ? "open" : ""} ${hasHere ? "has-here" : ""}`} key={g.key} aria-label={g.title}>
              {!g.flat && (
                <button type="button" className="nav-head" aria-expanded={expanded} aria-controls={`nav-${g.key}`} onClick={() => setOpenKey(expanded ? "" : g.key)}>
                  <span className="nav-ico"><g.icon size={16} aria-hidden /></span>
                  <span className="nav-head-t">{g.title}</span>
                  {!expanded && sum > 0 && <span className="count" title="Требует внимания">{sum}</span>}
                  <ChevronDown className="chev" size={15} aria-hidden />
                </button>
              )}
              <div className="nav-body" id={`nav-${g.key}`} inert={!expanded}>
                <div className="nav-inner">
                  {g.items.map((i) => {
                    const cap = i.sub && i.sub !== lastSub ? i.sub : "";
                    if (i.sub) lastSub = i.sub;
                    const n = badgeOf(i.badge);
                    return (
                      <div key={i.to}>
                        {cap && <div className="nav-sub">{cap}</div>}
                        <NavLink to={i.to} end={i.end} title={i.label} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
                          <i.icon size={17} aria-hidden />
                          <span className="nav-label">{i.label}</span>
                          {n > 0 && <span className="count" title={i.badge === "pending" ? "Ждут проверки" : i.badge === "reminders" ? "Наступило" : "Адресовано вам, не открыто"}>{n}</span>}
                        </NavLink>
                      </div>
                    );
                  })}
                </div>
              </div>
            </nav>
          );
        })}
      </aside>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}

      <div className="main">
        <header className="topbar">
          <button className="btn ghost icon-btn burger" onClick={() => setOpen(true)} aria-label="Открыть меню"><Menu size={20} /></button>
          {hereTitle && (
            <nav className="where" aria-label="Вы находитесь">
              {here && !here.group.flat && <><span className="where-group">{here.group.title}</span><ChevronRight size={14} aria-hidden className="where-sep" /></>}
              <strong aria-current="page">{hereTitle}</strong>
            </nav>
          )}
          {canSearch && (
            <form className="search" role="search" onSubmit={(e) => { e.preventDefault(); nav("/vehicles" + (q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "")); }}>
              <Search size={16} aria-hidden />
              <input className="input" placeholder="Найти технику по госномеру или марке" aria-label="Поиск техники" value={q} onChange={(e) => setQ(e.target.value)} />
            </form>
          )}
          <span className="spacer" />
          <button type="button" className="btn ghost icon-btn qnav-btn" onClick={() => setQuick(true)} aria-label="Найти раздел" title="Найти раздел (Ctrl+K)"><Search size={18} aria-hidden /></button>
          <ThemeToggle />
          <BellMenu />
          {me!.legal_entities.length > 1 && (
            <select className="input compact topbar-entity" aria-label="Юридическое лицо" value={entityId ?? ""} onChange={(e) => setEntityId(e.target.value ? Number(e.target.value) : null)}>
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
      {quick && <QuickNav groups={groups} onClose={() => setQuick(false)} onPick={(to) => { setQuick(false); setOpen(false); nav(to); }} />}
    </div>
  );
}

/** Быстрый поиск раздела: начните вводить название («счета», «табель», «машины»), стрелки выбирают, Enter открывает. */
function QuickNav({ groups, onClose, onPick }: { groups: Group[]; onClose: () => void; onPick: (to: string) => void }) {
  const [text, setText] = useState("");
  const [cur, setCur] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  type Hit = { i: Item; g: Group; hay: string };
  const all = useMemo<Hit[]>(() => groups.flatMap((g) => g.items.map((i) => ({ i, g, hay: `${i.label} ${g.title} ${i.sub ?? ""} ${i.words ?? ""}`.toLowerCase() }))), [groups]);
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  const found = useMemo<Hit[]>(() => {
    if (!words.length) return all;
    const label = (x: Hit) => x.i.label.toLowerCase();
    // сначала те, у кого название начинается с запроса, потом где слово есть в названии, потом остальные
    const rank = (x: Hit) => (label(x).startsWith(words[0]) ? 0 : words.every((w) => label(x).includes(w)) ? 1 : 2);
    return all.filter((x) => words.every((w) => x.hay.includes(w))).map((x, n) => ({ x, n })).sort((a, b) => rank(a.x) - rank(b.x) || a.n - b.n).map((r) => r.x);
  }, [all, text]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => setCur(0), [text]);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    input.current?.focus();
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; prev?.focus?.(); };
  }, []);
  useEffect(() => { listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [cur, found]);

  const key = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setCur((c) => Math.min(c + 1, found.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCur((c) => Math.max(c - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); const f = found[cur]; if (f) onPick(f.i.to); }
    else if (e.key === "Tab") e.preventDefault();   // в окне один элемент ввода: фокус не уходит на страницу под ним
  };
  const down = useRef(false);
  let lastGroup = "";
  return (
    <div className="overlay qnav-overlay" onMouseDown={(e) => (down.current = e.target === e.currentTarget)} onClick={(e) => down.current && e.target === e.currentTarget && onClose()}>
      <div className="qnav" role="dialog" aria-modal="true" aria-label="Найти раздел" onKeyDown={key}>
        <div className="qnav-input">
          <Search size={18} aria-hidden />
          <input ref={input} value={text} onChange={(e) => setText(e.target.value)} placeholder="Что открыть? Например: счета, табель, машины"
            role="combobox" aria-expanded="true" aria-controls="qnav-list" aria-activedescendant={found[cur] ? `qnav-${cur}` : undefined} aria-label="Название раздела" autoComplete="off" />
          <kbd aria-hidden>Esc</kbd>
        </div>
        {found.length === 0 ? (
          <p className="qnav-none">Такого раздела нет или у вас нет к нему доступа. Попробуйте другое слово.</p>
        ) : (
          <ul className="qnav-list" id="qnav-list" role="listbox" aria-label="Разделы" ref={listRef}>
            {found.map((f, n) => {
              const head = !words.length && f.g.title !== lastGroup ? f.g.title : "";
              lastGroup = f.g.title;
              return (
                <li key={f.i.to} role="presentation">
                  {head && <div className="qnav-head" aria-hidden>{head}</div>}
                  <div id={`qnav-${n}`} role="option" aria-selected={n === cur} className={`qnav-item ${n === cur ? "on" : ""}`} onMouseMove={() => cur !== n && setCur(n)} onClick={() => onPick(f.i.to)}>
                    <f.i.icon size={17} aria-hidden />
                    <span className="qnav-label">{f.i.label}</span>
                    <span className="qnav-where">{f.g.flat ? "" : f.g.title}</span>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        <div className="qnav-foot muted small" aria-hidden><span><kbd>↑</kbd><kbd>↓</kbd> выбрать</span><span><kbd>Enter</kbd> открыть</span></div>
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
