// Все уведомления текущего пользователя: что случилось, когда, и куда перейти.
import { useQuery } from "@tanstack/react-query";
import { BellOff, CheckCheck } from "lucide-react";
import { get } from "../api";
import { useUrlState } from "../hooks";
import { Button, Empty, FilterChips, PageHeader, Pagination, QueryState } from "../ui";
import { NoticeRow, useOpenNotice, useReadAll, type NoticeList } from "../components/Bell";

const SIZE = 30;

export function Notifications() {
  const [f, setF] = useUrlState({ show: "", page: "1" });
  const page = Number(f.page) || 1;
  const params = { unread: f.show === "unread", page, page_size: SIZE };
  const q = useQuery({ queryKey: ["notifications", "list", params], queryFn: () => get<NoticeList>("/notifications", params), placeholderData: (p) => p, refetchInterval: 60_000 });
  const open = useOpenNotice();
  const readAll = useReadAll();
  const d = q.data;
  return (
    <>
      <PageHeader title="Уведомления" sub="Новые обращения, ответы, задания, объявления и напоминания по срокам. Каждый видит только свои"
        actions={<Button icon={CheckCheck} onClick={() => readAll.mutate()} loading={readAll.isPending} disabled={!d?.unread}>Прочитать все</Button>} />
      <div className="toolbar">
        <FilterChips value={f.show} onChange={(v) => setF({ show: v, page: "1" })}
          options={[{ id: "", label: "Все" }, { id: "unread", label: "Непрочитанные", count: d?.unread }]} />
      </div>
      <QueryState q={q}>
        {d && (d.items.length === 0
          ? <Empty icon={BellOff} title={f.show === "unread" ? "Непрочитанных нет" : "Уведомлений пока нет"}>
              {f.show === "unread" ? "Всё прочитано." : "Они появятся, когда кто-то напишет вам, назначит задание или подойдёт срок документа."}
            </Empty>
          : <div className="card flush">
              <div className="notice-list">
                {d.items.map((n) => <NoticeRow key={n.id} n={n} onOpen={() => open(n)} />)}
              </div>
              <Pagination page={page} pageSize={SIZE} total={d.total} onPage={(p) => setF({ page: String(p) })} />
            </div>)}
      </QueryState>
    </>
  );
}
