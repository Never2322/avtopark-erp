// «Мои объявления»: что адресовано лично мне, и ответ автору.
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Megaphone, Send } from "lucide-react";
import { errorText, get, post } from "../api";
import { fmtDateTime } from "../format";
import { Badge, Card, Empty, NotFound, PageHeader, QueryState, Button, useToast } from "../ui";

interface Item { id: number; title: string; author: string; created_at: string; read: boolean; messages: number; preview: string }
interface Msg { id: number; author: string; from_staff: boolean; text: string; created_at: string }
interface One { id: number; title: string; body: string; author: string; created_at: string; thread: Msg[] }

export function News() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["news", "list"], queryFn: () => get<{ items: Item[] }>("/news"), refetchInterval: 60_000 });
  return (
    <>
      <PageHeader title="Мои объявления" sub="Сообщения от руководства и диспетчеров. Можно ответить прямо здесь" />
      <QueryState q={q}>
        {q.data && (q.data.items.length === 0
          ? <Empty icon={Megaphone} title="Объявлений нет">Когда вам что-то адресуют, это появится здесь и в колокольчике сверху.</Empty>
          : <div className="stack-sm">
              {q.data.items.map((n) => (
                <button key={n.id} className={`news-card ${n.read ? "" : "unread"}`} onClick={() => nav(`/news/${n.id}`)}>
                  <span className="row wrap" style={{ gap: 8 }}>
                    <strong>{n.title}</strong>
                    {!n.read && <Badge tone="warning">Новое</Badge>}
                    {n.messages > 0 && <Badge tone="info">Сообщений: {n.messages}</Badge>}
                  </span>
                  {n.preview && <span className="muted">{n.preview}</span>}
                  <span className="muted small">{n.author} · {fmtDateTime(n.created_at)}</span>
                </button>
              ))}
            </div>)}
      </QueryState>
    </>
  );
}

export function NewsCard() {
  const { id } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const q = useQuery({ queryKey: ["news", "one", id], queryFn: async () => {
    const r = await get<One>(`/news/${id}`);
    qc.invalidateQueries({ queryKey: ["notif-summary"] });
    qc.invalidateQueries({ queryKey: ["notifications"] });
    qc.invalidateQueries({ queryKey: ["news", "list"] });
    return r;
  }, retry: (n, e) => (e as { status?: number })?.status !== 404 && n < 2, refetchInterval: 30_000 });
  const send = useMutation({
    mutationFn: () => post(`/news/${id}/reply`, { text }),
    onSuccess: () => { setText(""); qc.invalidateQueries({ queryKey: ["news"] }); toast.success("Ответ отправлен"); },
    onError: (e) => toast.error(errorText(e)),
  });
  if ((q.error as { status?: number } | null)?.status === 404) return <NotFound what="Объявление" text="Его закрыли или убрали в архив." />;
  const a = q.data;
  return (
    <QueryState q={q}>
      {a && (
        <>
          <PageHeader crumb={<Link to="/news">← Мои объявления</Link>} title={a.title} sub={<>{a.author} · {fmtDateTime(a.created_at)}</>} />
          <div className="grid cols-2" style={{ alignItems: "start" }}>
            <Card title="Текст">{a.body ? <div style={{ whiteSpace: "pre-wrap" }}>{a.body}</div> : <span className="muted">Только заголовок, без текста.</span>}</Card>
            <Card title="Ваша переписка с автором">
              <div className="stack-sm">
                {a.thread.length === 0 && <span className="muted small">Вы ещё не отвечали. Напишите, если нужно что-то уточнить или подтвердить.</span>}
                {a.thread.map((m) => (
                  <div key={m.id} className={`bubble ${m.from_staff ? "staff" : "mine"}`}>
                    <div style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>
                    <div className="muted small">{m.from_staff ? m.author : "Вы"} · {fmtDateTime(m.created_at)}</div>
                  </div>
                ))}
                <form className="row" style={{ alignItems: "flex-start", marginTop: 6 }} onSubmit={(e) => { e.preventDefault(); if (text.trim()) send.mutate(); }}>
                  <textarea className="input" style={{ minHeight: 44 }} aria-label="Ответ" placeholder="Написать ответ" maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} />
                  <Button type="submit" variant="primary" icon={Send} loading={send.isPending} disabled={!text.trim()} aria-label="Отправить ответ" />
                </form>
              </div>
            </Card>
          </div>
        </>
      )}
    </QueryState>
  );
}
