// Колокольчик в верхней строке: непрочитанные уведомления, последние из них и переход к тому, о чём они.
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, CheckCheck } from "lucide-react";
import { get, post } from "../api";
import { fmtDateTime } from "../format";
import { Badge, Spinner, type Tone } from "../ui";

export interface Notice { id: number; kind: string; kind_label: string; title: string; text: string; url: string; created_at: string; read: boolean }
export interface NoticeList { items: Notice[]; total: number; page: number; page_size: number; unread: number }
export interface NotifSummary { unread: number; news_unread: number }

export const KIND_TONE: Record<string, Tone> = { request: "info", reply: "mint", announce: "warning", job: "success", parts: "success", reminder: "error", info: "neutral" };

/** Сколько непрочитанных уведомлений и объявлений. Обновляется раз в минуту и при возврате на вкладку. */
export function useNotifSummary() {
  return useQuery({ queryKey: ["notif-summary"], queryFn: () => get<NotifSummary>("/notifications/summary"), refetchInterval: 60_000, staleTime: 20_000 });
}

/** Отметить прочитанным и открыть то, о чём уведомление. */
export function useOpenNotice() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const mark = useMutation({
    mutationFn: (id: number) => post(`/notifications/${id}/read`),
    onSettled: () => { qc.invalidateQueries({ queryKey: ["notif-summary"] }); qc.invalidateQueries({ queryKey: ["notifications"] }); },
  });
  return (n: Notice, after?: () => void) => {
    if (!n.read) mark.mutate(n.id);
    after?.();
    if (n.url) nav(n.url);
  };
}

export function useReadAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post("/notifications/read-all"),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["notif-summary"] }); qc.invalidateQueries({ queryKey: ["notifications"] }); },
  });
}

export function NoticeRow({ n, onOpen }: { n: Notice; onOpen: () => void }) {
  return (
    <button className={`notice ${n.read ? "" : "unread"}`} onClick={onOpen}>
      <span className="notice-dot" aria-hidden />
      <span className="notice-main">
        <span className="row wrap" style={{ gap: 6 }}>
          <Badge tone={KIND_TONE[n.kind] ?? "neutral"}>{n.kind_label || "Сообщение"}</Badge>
          <span className="muted small">{fmtDateTime(n.created_at)}</span>
          {!n.read && <span className="sr-only">Не прочитано</span>}
        </span>
        <strong className="notice-title">{n.title}</strong>
        {n.text && <span className="notice-text muted small">{n.text}</span>}
      </span>
    </button>
  );
}

export function BellMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const sum = useNotifSummary();
  const unread = sum.data?.unread ?? 0;
  const open1 = useOpenNotice();
  const readAll = useReadAll();
  const list = useQuery({
    queryKey: ["notifications", "bell"],
    queryFn: () => get<NoticeList>("/notifications", { page_size: 8 }),
    enabled: open,
    refetchOnMount: "always",
  });

  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, []);

  return (
    <div className="bell" ref={ref}>
      <button className="btn ghost icon-btn bell-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-haspopup="dialog"
        aria-label={unread ? `Уведомления: непрочитанных ${unread}` : "Уведомления"} title="Уведомления">
        <Bell size={19} aria-hidden />
        {unread > 0 && <span className="bell-count">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div className="bell-pop" role="dialog" aria-label="Уведомления">
          <div className="bell-head">
            <strong>Уведомления</strong>
            <button className="linklike" onClick={() => readAll.mutate()} disabled={!unread || readAll.isPending}><CheckCheck size={14} aria-hidden /> Прочитать все</button>
          </div>
          <div className="bell-list">
            {list.isLoading && <Spinner />}
            {list.data && list.data.items.length === 0 && <div className="bell-empty muted">Пока уведомлений нет. Здесь появятся новые обращения, ответы, наряды и напоминания.</div>}
            {list.data?.items.map((n) => <NoticeRow key={n.id} n={n} onOpen={() => open1(n, () => setOpen(false))} />)}
          </div>
          <div className="bell-foot"><Link to="/notifications" onClick={() => setOpen(false)}>Все уведомления</Link></div>
        </div>
      )}
    </div>
  );
}
