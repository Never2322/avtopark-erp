// Окно «Загрузить счёт»: приложить файл или фото и, если хочется, написать заметку. Остальное программа читает сама.
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileUp, Paperclip, X } from "lucide-react";
import { api } from "../api";
import { fmtSize } from "../format";
import { Alert, Button, Field, Modal, useToast } from "../ui";
import { FIELD_LABELS, giveHandoff, type AutoResult } from "./billAuto";
import type { BillMeta } from "./Bills";

const MAX_FILES = 10;

export function BillAuto({ meta, entity, onClose }: { meta: BillMeta; entity?: number; onClose: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [note, setNote] = useState("");
  const [legal, setLegal] = useState(entity && meta.entities.some((x) => x.id === entity) ? String(entity) : "");
  const [duplicate, setDuplicate] = useState<AutoResult["duplicate"]>(null);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const m = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      files.forEach((x) => fd.append("files", x));
      fd.append("note", note);
      if (legal) fd.append("legal_entity_id", legal);
      return api<AutoResult>("POST", "/bills/auto", fd);
    },
    onSuccess: (r) => {
      setError(""); setDuplicate(null);
      if (r.created && r.bill) {
        qc.invalidateQueries();
        toast.success(`Счёт № ${r.bill.number} внесён`);
        onClose();
        nav(`/bills/${r.bill.id}`, { state: { auto: { how: r.how, note: r.note } } });
        return;
      }
      if (r.duplicate) { setDuplicate(r.duplicate); return; }
      // Не всё удалось прочитать или сумма не подтвердилась: открываем форму с тем, что есть, файлы уже приложены
      const missing = r.missing ?? [];
      const what = missing.map((k) => FIELD_LABELS[k] ?? k).join(", ");
      giveHandoff({
        found: r.found ?? {}, files, note, missing, errors: r.errors ?? {}, legalEntityId: legal,
        message: r.note || (what ? `Не удалось прочитать: ${what}. Впишите это и нажмите «Внести счёт».` : "Проверьте данные и нажмите «Внести счёт»."),
      });
      onClose();
      nav("/bills/new?kind=uploaded");
    },
    onError: (e: any) => setError(e.message || "Не получилось отправить счёт"),
  });

  const pick = (list: FileList | null) => {
    if (!list) return;
    setFiles((s) => [...s, ...list].slice(0, MAX_FILES));
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <Modal title="Загрузить счёт" onClose={() => !m.isPending && onClose()}
      footer={<>
        <Button onClick={onClose} disabled={m.isPending}>Отмена</Button>
        <Button variant="primary" icon={FileUp} loading={m.isPending} disabled={!files.length} onClick={() => m.mutate()}>{m.isPending ? "Читаю счёт…" : "Прочитать и внести"}</Button>
      </>}>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          Приложите файл счёта: PDF, скан или фото. Программа сама прочитает номер, дату, сумму, поставщика и юрлицо и внесёт счёт. Если что-то не прочитается, откроется форма, где останется только дописать.
        </p>
        <div className="stack-sm">
          <div className="row wrap">
            <Button icon={Paperclip} onClick={() => fileRef.current?.click()} disabled={m.isPending}>{files.length ? "Добавить ещё файл" : "Выбрать файл или сфотографировать"}</Button>
            <input ref={fileRef} type="file" hidden multiple accept="application/pdf,image/jpeg,image/png,image/webp" onChange={(ev) => pick(ev.target.files)} />
            <span className="muted small">до {MAX_FILES} файлов, каждый до 10 МБ. Для счёта из нескольких страниц приложите все страницы.</span>
          </div>
          {files.length > 0 && (
            <ul className="file-list">
              {files.map((x, i) => (
                <li key={i}><Paperclip size={14} aria-hidden /> <span className="grow">{x.name} <span className="muted small">{fmtSize(x.size)}</span></span>
                  <Button variant="ghost" size="sm" icon={X} aria-label={`Убрать файл ${x.name}`} disabled={m.isPending} onClick={() => setFiles((s) => s.filter((_, k) => k !== i))} />
                </li>
              ))}
            </ul>
          )}
        </div>
        {meta.entities.length > 1 && (
          <Field label="Юридическое лицо" hint="Можно не выбирать: программа найдёт его по ИНН покупателя в счёте">
            <select className="input" value={legal} disabled={m.isPending} onChange={(ev) => setLegal(ev.target.value)}>
              <option value="">Определить по счёту</option>
              {meta.entities.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
          </Field>
        )}
        <Field label="Заметка (по желанию)" hint="Например: «замена масла на КАМАЗе, срочно». Ляжет в поле «Заметка» счёта.">
          <textarea className="input" value={note} maxLength={500} disabled={m.isPending} onChange={(ev) => setNote(ev.target.value)} />
        </Field>
        {m.isPending && <Alert tone="info">Читаю счёт. С PDF это пара секунд, со скана или фото может занять до минуты. Не закрывайте окно.</Alert>}
        {error && <Alert tone="error">{error}</Alert>}
        {duplicate && (
          <Alert tone="warning">Такой счёт уже внесён: <Link to={`/bills/${duplicate.id}`} onClick={onClose}>счёт № {duplicate.number}</Link>. Второй раз он не заносится.</Alert>
        )}
      </div>
    </Modal>
  );
}
