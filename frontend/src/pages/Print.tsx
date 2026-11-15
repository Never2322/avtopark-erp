// Печатные формы: заявка, счёт, акт, накладная, УПД и кассовые ордера. Данные и суммы прописью считает сервер.
// Формы упрощённые: их удобно распечатать и подписать, но юридически обязательные бланки (УПД, ТН) стоит сверить с бухгалтером.
import { useEffect, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Printer } from "lucide-react";
import { get } from "../api";
import { fmtDateLong, fmtDateTime, fmtMoney, fmtNum } from "../format";
import { Alert, Button, QueryState } from "../ui";

type P = Record<string, any>;

const party = (x: P) => [x.name, x.inn && `ИНН ${x.inn}`, x.kpp && `КПП ${x.kpp}`, x.address, x.phone && `тел. ${x.phone}`].filter(Boolean).join(", ");
const blank = (v?: string | null) => (v ? v : <span className="blank">&nbsp;</span>);

function Sign({ role, name }: { role: string; name?: string }) {
  return (
    <div className="sign">
      <span>{role}</span>
      <span className="line" />
      <span className="decode">{name ? `/ ${name} /` : "/ ______________ /"}</span>
    </div>
  );
}

function Totals({ d }: { d: P }) {
  return (
    <table className="totals">
      <tbody>
        <tr><td>Итого без НДС:</td><td className="num">{fmtMoney(d.amount)}</td></tr>
        <tr><td>{d.vat_pct ? `НДС ${d.vat_pct} %:` : "НДС:"}</td><td className="num">{d.vat_pct ? fmtMoney(d.vat) : "без НДС"}</td></tr>
        <tr className="strong"><td>Всего к оплате:</td><td className="num">{fmtMoney(d.total)}</td></tr>
      </tbody>
    </table>
  );
}

function Items({ d }: { d: P }) {
  return (
    <table className="ptable">
      <thead><tr><th style={{ width: 32 }}>№</th><th>Наименование</th><th style={{ width: 70 }}>Кол-во</th><th style={{ width: 56 }}>Ед.</th><th style={{ width: 100 }}>Цена, ₽</th><th style={{ width: 110 }}>Сумма, ₽</th></tr></thead>
      <tbody>
        <tr><td className="c">1</td><td>{d.subject}</td><td className="num">{fmtNum(d.qty, 3)}</td><td className="c">{d.unit}</td><td className="num">{fmtMoney(d.price)}</td><td className="num">{fmtMoney(d.amount)}</td></tr>
      </tbody>
    </table>
  );
}

function Words({ d }: { d: P }) {
  return <p className="words">Всего наименований 1, на сумму <strong>{fmtMoney(d.total)} ₽</strong>.<br /><strong>{d.words}</strong>{d.vat_pct ? `, в том числе НДС ${d.vat_pct} % — ${fmtMoney(d.vat)} ₽` : ", без НДС"}.</p>;
}

function Title({ children, sub }: { children: ReactNode; sub?: ReactNode }) {
  return <div className="doc-title"><h1>{children}</h1>{sub && <div className="doc-sub">{sub}</div>}</div>;
}

/* ---------------- формы ---------------- */

function Invoice({ d }: { d: P }) {
  const s = d.seller;
  return (
    <>
      <table className="ptable bank">
        <tbody>
          <tr><td rowSpan={2} colSpan={2}>{blank(s.bank_name)}<div className="cap">Банк получателя</div></td><td className="cap-l">БИК</td><td>{blank(s.bik)}</td></tr>
          <tr><td className="cap-l">Корр. сч. №</td><td>{blank(s.corr_account)}</td></tr>
          <tr><td>ИНН {blank(s.inn)}</td><td>КПП {blank(s.kpp)}</td><td rowSpan={2} className="cap-l">Сч. №</td><td rowSpan={2}>{blank(s.account)}</td></tr>
          <tr><td colSpan={2}>{blank(s.name)}<div className="cap">Получатель</div></td></tr>
        </tbody>
      </table>
      <Title>Счёт на оплату № {d.number} от {fmtDateLong(d.date)}</Title>
      <table className="parties">
        <tbody>
          <tr><th>Поставщик:</th><td>{party(s)}</td></tr>
          <tr><th>Покупатель:</th><td>{party(d.buyer)}</td></tr>
          {d.order && <tr><th>Основание:</th><td>Заказ № {d.order.number} от {fmtDateLong(d.order.date)}</td></tr>}
        </tbody>
      </table>
      <Items d={d} />
      <Totals d={d} />
      <Words d={d} />
      <div className="signs"><Sign role="Руководитель" name={s.head_name} /><Sign role="Главный бухгалтер" name={s.accountant_name} /></div>
    </>
  );
}

function Act({ d }: { d: P }) {
  const s = d.seller;
  return (
    <>
      <Title>Акт № {d.number} от {fmtDateLong(d.date)}</Title>
      <p className="center">об оказании услуг{d.order ? ` по заказу № ${d.order.number} от ${fmtDateLong(d.order.date)}` : ""}</p>
      <table className="parties">
        <tbody>
          <tr><th>Исполнитель:</th><td>{party(s)}</td></tr>
          <tr><th>Заказчик:</th><td>{party(d.buyer)}</td></tr>
        </tbody>
      </table>
      <Items d={d} />
      <Totals d={d} />
      <Words d={d} />
      <p>Вышеперечисленные услуги выполнены полностью и в срок. Заказчик претензий по объёму, качеству и срокам оказания услуг не имеет.</p>
      <div className="signs two">
        <div><strong>Исполнитель</strong><Sign role="Руководитель" name={s.head_name} /></div>
        <div><strong>Заказчик</strong><Sign role="Представитель" /></div>
      </div>
    </>
  );
}

function Upd({ d }: { d: P }) {
  const s = d.seller;
  return (
    <>
      <Title sub="Статус: 1 — счёт-фактура и передаточный документ (акт)">Универсальный передаточный документ № {d.number} от {fmtDateLong(d.date)}</Title>
      <table className="parties">
        <tbody>
          <tr><th>Продавец:</th><td>{party(s)}</td></tr>
          <tr><th>Покупатель:</th><td>{party(d.buyer)}</td></tr>
          <tr><th>Валюта:</th><td>Российский рубль, код 643</td></tr>
          {d.order && <tr><th>Основание:</th><td>Заказ № {d.order.number} от {fmtDateLong(d.order.date)}</td></tr>}
        </tbody>
      </table>
      <table className="ptable">
        <thead><tr><th>№</th><th>Наименование товара (услуги)</th><th>Ед.</th><th>Кол-во</th><th>Цена, ₽</th><th>Сумма без НДС, ₽</th><th>НДС</th><th>Сумма НДС, ₽</th><th>Всего, ₽</th></tr></thead>
        <tbody><tr><td className="c">1</td><td>{d.subject}</td><td className="c">{d.unit}</td><td className="num">{fmtNum(d.qty, 3)}</td><td className="num">{fmtMoney(d.price)}</td><td className="num">{fmtMoney(d.amount)}</td><td className="c">{d.vat_pct ? `${d.vat_pct} %` : "без НДС"}</td><td className="num">{d.vat_pct ? fmtMoney(d.vat) : "—"}</td><td className="num">{fmtMoney(d.total)}</td></tr></tbody>
        <tfoot><tr><td colSpan={5}><strong>Всего к оплате</strong></td><td className="num">{fmtMoney(d.amount)}</td><td className="c">×</td><td className="num">{d.vat_pct ? fmtMoney(d.vat) : "—"}</td><td className="num"><strong>{fmtMoney(d.total)}</strong></td></tr></tfoot>
      </table>
      <p className="words"><strong>{d.words}</strong></p>
      <div className="signs two">
        <div><strong>Продавец</strong><Sign role="Руководитель" name={s.head_name} /><Sign role="Главный бухгалтер" name={s.accountant_name} /></div>
        <div><strong>Покупатель</strong><Sign role="Представитель" /><p className="cap">Дата получения: «___» ____________ 20__ г.</p></div>
      </div>
    </>
  );
}

function Tn({ d }: { d: P }) {
  const s = d.seller, o = d.order;
  return (
    <>
      <Title>Транспортная накладная № {d.number} от {fmtDateLong(d.date)}</Title>
      <table className="parties">
        <tbody>
          <tr><th>Грузоотправитель:</th><td>{party(d.buyer)}</td></tr>
          <tr><th>Грузополучатель:</th><td>{party(d.buyer)}</td></tr>
          <tr><th>Перевозчик:</th><td>{party(s)}</td></tr>
          <tr><th>Пункт погрузки:</th><td>{blank(o?.point_from)}</td></tr>
          <tr><th>Пункт разгрузки:</th><td>{blank(o?.point_to)}</td></tr>
          <tr><th>Транспортное средство:</th><td>{blank(o?.vehicle)}</td></tr>
          <tr><th>Водитель:</th><td>{blank(o?.driver)}</td></tr>
        </tbody>
      </table>
      <table className="ptable">
        <thead><tr><th>№</th><th>Наименование груза</th><th>Масса, кг</th><th>Расстояние, км</th><th>Стоимость перевозки, ₽</th></tr></thead>
        <tbody><tr><td className="c">1</td><td>{o?.cargo || d.subject}</td><td className="num">{o?.cargo_kg != null ? fmtNum(o.cargo_kg, 1) : ""}</td><td className="num">{o?.distance_km ?? ""}</td><td className="num">{fmtMoney(d.total)}</td></tr></tbody>
      </table>
      <p className="words"><strong>{d.words}</strong>{d.vat_pct ? `, в том числе НДС ${d.vat_pct} % — ${fmtMoney(d.vat)} ₽` : ", без НДС"}.</p>
      <div className="signs three">
        <div><strong>Грузоотправитель</strong><Sign role="Подпись" /></div>
        <div><strong>Водитель</strong><Sign role="Подпись" name={o?.driver} /></div>
        <div><strong>Грузополучатель</strong><Sign role="Подпись" /></div>
      </div>
    </>
  );
}

function OrderSheet({ d }: { d: P }) {
  const rows: [string, ReactNode][] = [
    ["Откуда", blank(d.point_from)], ["Куда", blank(d.point_to)], ["Груз или работа", blank(d.cargo)],
    ["Масса груза", d.cargo_kg != null ? `${fmtNum(d.cargo_kg, 1)} кг` : "—"], ["Расстояние", d.distance_km != null ? `${d.distance_km} км` : "—"],
    ["Время в работе", d.hours != null ? `${fmtNum(d.hours, 2)} ч` : "—"], ["Выезд", d.depart_at ? fmtDateTime(d.depart_at) : "—"], ["Доставка", d.deliver_at ? fmtDateTime(d.deliver_at) : "—"],
    ["Техника", blank(d.vehicle)], ["Водитель", blank(d.driver)],
  ];
  return (
    <>
      <Title sub={`Статус: ${d.status}`}>Заявка на перевозку № {d.number} от {fmtDateLong(d.date)}</Title>
      <table className="parties">
        <tbody>
          <tr><th>Исполнитель:</th><td>{party(d.seller)}</td></tr>
          <tr><th>Заказчик:</th><td>{party(d.customer)}</td></tr>
        </tbody>
      </table>
      <table className="ptable pkv"><tbody>{rows.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
      <table className="ptable pkv">
        <tbody>
          <tr><th>Тариф</th><td>{d.tariff || "—"}</td></tr>
          {d.qty != null && <tr><th>Количество × ставка</th><td>{fmtNum(d.qty, 3)} × {fmtMoney(d.rate)} ₽</td></tr>}
          <tr><th>Стоимость без НДС</th><td>{fmtMoney(d.amount)} ₽</td></tr>
          <tr><th>{d.vat_pct ? `НДС ${d.vat_pct} %` : "НДС"}</th><td>{d.vat_pct ? `${fmtMoney(d.vat)} ₽` : "без НДС"}</td></tr>
          <tr className="strong"><th>Итого</th><td>{fmtMoney(d.total)} ₽</td></tr>
        </tbody>
      </table>
      <p className="words"><strong>{d.words}</strong></p>
      {d.note && <p><strong>Примечание:</strong> {d.note}</p>}
      <div className="signs two">
        <div><strong>Исполнитель</strong><Sign role="Руководитель" name={d.seller.head_name} /></div>
        <div><strong>Заказчик</strong><Sign role="Представитель" /></div>
      </div>
    </>
  );
}

function CashOrder({ d }: { d: P }) {
  const incoming = d.cash_kind === "in";
  return (
    <>
      <Title sub={`${d.seller.name}${d.seller.inn ? `, ИНН ${d.seller.inn}` : ""}`}>{d.title} № {d.prefix}-{d.number}/{d.year}</Title>
      <p className="center">от {fmtDateLong(d.date)}</p>
      <table className="ptable pkv">
        <tbody>
          <tr><th>Касса (счёт)</th><td>{d.account}</td></tr>
          <tr><th>{incoming ? "Принято от" : "Выдать"}</th><td>{blank(d.who)}</td></tr>
          <tr><th>Основание</th><td>{blank(d.basis)}</td></tr>
          <tr className="strong"><th>Сумма</th><td>{fmtMoney(d.amount)} ₽</td></tr>
        </tbody>
      </table>
      <p className="words"><strong>{d.words}</strong></p>
      <div className="signs two">
        <div><Sign role="Руководитель" name={d.seller.head_name} /><Sign role="Главный бухгалтер" name={d.seller.accountant_name} /></div>
        <div><Sign role="Кассир" /><Sign role={incoming ? "Плательщик" : "Получатель"} /></div>
      </div>
    </>
  );
}

function RepairBillSheet({ d }: { d: P }) {
  const s = d.supplier;
  return (
    <>
      <table className="ptable bank">
        <tbody>
          <tr><td rowSpan={2} colSpan={2}>{blank(s.bank_name)}<div className="cap">Банк получателя</div></td><td className="cap-l">БИК</td><td>{blank(s.bik)}</td></tr>
          <tr><td className="cap-l">Корр. сч. №</td><td>{blank(s.corr_account)}</td></tr>
          <tr><td>ИНН {blank(s.inn)}</td><td>КПП {blank(s.kpp)}</td><td rowSpan={2} className="cap-l">Сч. №</td><td rowSpan={2}>{blank(s.account)}</td></tr>
          <tr><td colSpan={2}>{blank(s.name)}<div className="cap">Получатель</div></td></tr>
        </tbody>
      </table>
      <Title>Счёт на оплату № {d.number} от {fmtDateLong(d.date)}</Title>
      <table className="parties">
        <tbody>
          <tr><th>Поставщик:</th><td>{party({ name: s.name, inn: s.inn, kpp: s.kpp, address: s.address, phone: s.phone })}</td></tr>
          <tr><th>Покупатель:</th><td>{party(d.buyer)}</td></tr>
          {d.basis && <tr><th>Основание:</th><td>{d.basis}</td></tr>}
          {d.vehicle && <tr><th>Машина:</th><td>{d.vehicle}</td></tr>}
        </tbody>
      </table>
      <table className="ptable">
        <thead><tr><th style={{ width: 32 }}>№</th><th>Наименование</th><th style={{ width: 70 }}>Кол-во</th><th style={{ width: 56 }}>Ед.</th><th style={{ width: 100 }}>Цена, ₽</th><th style={{ width: 110 }}>Сумма, ₽</th></tr></thead>
        <tbody>
          {d.items.map((i: P) => (
            <tr key={i.pos}><td className="c">{i.pos}</td><td>{i.name}{i.article ? `, арт. ${i.article}` : ""}</td><td className="num">{i.qty}</td><td className="c">{i.unit}</td><td className="num">{fmtMoney(i.price)}</td><td className="num">{fmtMoney(i.amount)}</td></tr>
          ))}
        </tbody>
      </table>
      <table className="totals">
        <tbody>
          <tr><td>{d.vat_pct ? `В том числе НДС ${d.vat_pct} %:` : "НДС:"}</td><td className="num">{d.vat_pct ? fmtMoney(d.vat) : "без НДС"}</td></tr>
          <tr className="strong"><td>Всего к оплате:</td><td className="num">{fmtMoney(d.total)}</td></tr>
        </tbody>
      </table>
      <p className="words">Всего наименований {d.items.length}, на сумму <strong>{fmtMoney(d.total)} ₽</strong>.<br /><strong>{d.words}</strong></p>
      {d.note && <p className="cap">{d.note}</p>}
      <div className="signs two">
        <div><strong>Поставщик</strong><Sign role="Руководитель" /></div>
        <div><strong>Покупатель</strong><Sign role="Руководитель" name={d.buyer.head_name} /></div>
      </div>
    </>
  );
}

/* ---------------- страница ---------------- */

const KINDS = new Set(["orders", "documents", "cash", "bills"]);

export function PrintPage() {
  const { kind = "", id = "" } = useParams();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["print", kind, id], queryFn: () => get<P>(`/print/${kind}/${id}`), enabled: KINDS.has(kind), retry: false });
  const d = q.data;
  useEffect(() => { if (d) document.title = `${d.title} № ${d.prefix ? `${d.prefix}-` : ""}${d.number}`; return () => { document.title = "Автопарк ERP"; }; }, [d]);

  let sheet: ReactNode = null;
  if (d?.kind === "order") sheet = <OrderSheet d={d} />;
  else if (d?.kind === "cash") sheet = <CashOrder d={d} />;
  else if (d?.kind === "repair_bill") sheet = <RepairBillSheet d={d} />;
  else if (d?.kind === "document") sheet = d.doc_type === "invoice" ? <Invoice d={d} /> : d.doc_type === "act" ? <Act d={d} /> : d.doc_type === "upd" ? <Upd d={d} /> : <Tn d={d} />;

  return (
    <div className="print-root">
      <div className="print-bar no-print">
        <Button icon={ArrowLeft} onClick={() => (window.history.length > 1 ? nav(-1) : nav("/"))}>Назад</Button>
        <Button variant="primary" icon={Printer} onClick={() => window.print()} disabled={!d}>Печать</Button>
        <span className="muted small">В окне печати можно выбрать «Сохранить как PDF».</span>
      </div>
      {!KINDS.has(kind) ? <Alert tone="error">Такой печатной формы нет.</Alert> : (
        <QueryState q={q}>
          {d && (
            <>
              {d.missing?.length > 0 && <Alert tone="warning"><span className="no-print">В реквизитах не хватает: {d.missing.join(", ")}. Заполните их в разделе «Юридические лица» (для заказчика — в «Контрагентах», для поставщика счёта на ремонт — в самом счёте), иначе в форме останутся пустые места.</span></Alert>}
              {d.archived && <Alert tone="warning"><span className="no-print">Запись в архиве: распечатывать её для работы не нужно.</span></Alert>}
              <div className={`sheet ${d.archived ? "archived" : ""}`}>{sheet}</div>
            </>
          )}
        </QueryState>
      )}
    </div>
  );
}
