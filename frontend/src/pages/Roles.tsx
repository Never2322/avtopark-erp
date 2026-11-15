import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus } from "lucide-react";
import { get, post, put } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Button, Card, Field, Modal, PageHeader, QueryState, useForm, useToast } from "../ui";

interface Role { id: number; code: string; name: string; description: string; is_system: boolean; permissions: string[]; users: number }
interface Perm { code: string; module: string; description: string }

function NewRole({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ name: "", description: "" });
  const m = useMutation({
    mutationFn: () => post("/roles", { ...f.values, permissions: [] }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["roles"] }); toast.success("Роль создана: отметьте её права в таблице"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Новая роль" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={f.values.name.trim().length < 2} onClick={() => { f.clear(); m.mutate(); }}>Создать</Button></>}>
      <div className="stack">
        {f.formError && !f.errors.name && <Alert tone="error">{f.formError}</Alert>}
        <Field label="Название" error={f.errors.name}><input className="input" value={f.values.name} maxLength={80} onChange={(e) => f.set("name", e.target.value)} /></Field>
        <Field label="Для кого" error={f.errors.description}><textarea className="input" value={f.values.description} maxLength={300} onChange={(e) => f.set("description", e.target.value)} /></Field>
        <div className="muted small">Новая роль создаётся без прав: «запрет по умолчанию». Нужные права отмечаются в таблице.</div>
      </div>
    </Modal>
  );
}

export function Roles() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const manage = can("roles.manage");
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => get<{ items: Role[] }>("/roles") });
  const perms = useQuery({ queryKey: ["permissions"], queryFn: () => get<{ items: Perm[] }>("/permissions"), staleTime: 10 * 60_000 });
  const [draft, setDraft] = useState<Record<number, Set<string>>>({});
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState<number | null>(null);

  useEffect(() => { if (roles.data) setDraft(Object.fromEntries(roles.data.items.map((r) => [r.id, new Set(r.permissions)]))); }, [roles.data]);

  const modules = useMemo(() => {
    const map = new Map<string, Perm[]>();
    perms.data?.items.forEach((p) => map.set(p.module, [...(map.get(p.module) ?? []), p]));
    return [...map.entries()];
  }, [perms.data]);

  const dirty = (r: Role) => {
    const d = draft[r.id];
    return !!d && (d.size !== r.permissions.length || r.permissions.some((c) => !d.has(c)));
  };
  const toggle = (r: Role, code: string) =>
    setDraft((s) => { const next = new Set(s[r.id]); next.has(code) ? next.delete(code) : next.add(code); return { ...s, [r.id]: next }; });
  const save = useMutation({
    mutationFn: (r: Role) => put(`/roles/${r.id}/permissions`, { permissions: [...draft[r.id]] }),
    onMutate: (r) => setSaving(r.id),
    onSuccess: (_d, r) => { qc.invalidateQueries({ queryKey: ["roles"] }); toast.success(`Права роли «${r.name}» сохранены. Они вступят в силу при следующем действии пользователей`); },
    onError: (e: any) => toast.error(e.message),
    onSettled: () => setSaving(null),
  });

  const items = roles.data?.items ?? [];
  const cols = items.length;

  return (
    <>
      <PageHeader title="Роли и права" sub="Что может делать каждая роль. Всё, что не отмечено, запрещено"
        actions={manage && <Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>Новая роль</Button>} />
      <Alert tone="info">Это стартовый набор прав: его нужно согласовать с руководителями (особенно для кладовщика, бухгалтера и директора). Изменения применяются сразу после сохранения.</Alert>
      <Card flush className="" >
        <QueryState q={roles}>
          <QueryState q={perms}>
            <div style={{ overflowX: "auto", maxHeight: "72vh" }}>
              <div className="perm-grid" style={{ ["--cols" as string]: cols, minWidth: 260 + cols * 108 }}>
                <div className="perm-row head">
                  <div>Право</div>
                  {items.map((r) => (
                    <div className="c" key={r.id} title={r.description}>
                      {r.name}
                      <div className="muted small" style={{ fontWeight: 400 }}>{r.users} польз.</div>
                    </div>
                  ))}
                </div>
                {modules.map(([module, list]) => (
                  <div key={module} style={{ display: "contents" }}>
                    <div className="perm-row module"><div style={{ gridColumn: `1 / span ${cols + 1}` }}>{module}</div></div>
                    {list.map((p) => (
                      <div className="perm-row" key={p.code}>
                        <div>{p.description}<div className="muted small mono">{p.code}</div></div>
                        {items.map((r) => {
                          const locked = r.code === "admin";
                          const checked = locked || !!draft[r.id]?.has(p.code);
                          return (
                            <div className="c" key={r.id}>
                              <input type="checkbox" style={{ width: 18, height: 18, accentColor: "var(--mint)" }} checked={checked} disabled={locked || !manage}
                                aria-label={`${r.name}: ${p.description}`} onChange={() => toggle(r, p.code)} />
                            </div>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                ))}
                <div className="perm-row" style={{ position: "sticky", bottom: 0, background: "#101b21", borderTop: "1px solid var(--border)" }}>
                  <div className="muted small">{manage ? "Сохраняйте каждую роль отдельно" : "Менять права может только администратор"}</div>
                  {items.map((r) => (
                    <div className="c" key={r.id}>
                      {r.code === "admin" ? <Badge icon={Lock}>Всегда всё</Badge> : manage && <Button size="sm" variant={dirty(r) ? "primary" : "default"} disabled={!dirty(r)} loading={saving === r.id} onClick={() => save.mutate(r)}>Сохранить</Button>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </QueryState>
        </QueryState>
      </Card>
      {creating && <NewRole onClose={() => setCreating(false)} />}
    </>
  );
}
