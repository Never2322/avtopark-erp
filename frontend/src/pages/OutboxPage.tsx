// Всё, что записано в телефоне и ещё не дошло до сервера.
import { OutboxList, useOnline } from "../components/Outbox";
import { Alert, PageHeader } from "../ui";

export function OutboxPage() {
  const online = useOnline();
  return (
    <>
      <PageHeader title="Ждут отправки" sub="Записи, сделанные без связи. Они лежат в телефоне и уходят сами, когда появляется сеть" />
      <div className="stack">
        {!online && <Alert tone="warning">Сейчас связи нет. Записи отправятся, когда сеть вернётся.</Alert>}
        <OutboxList title="В телефоне" always />
        <p className="muted small">Если запись не принята, внутри написана причина: например, пробег меньше прошлого. Уберите её отсюда и внесите заново с верными данными.
          Пока запись не ушла, не удаляйте программу с телефона и не выходите из неё на общем телефоне.</p>
      </div>
    </>
  );
}
