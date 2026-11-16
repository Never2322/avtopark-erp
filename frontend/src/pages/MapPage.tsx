// Карта 2ГИС: поиск адреса и адреса заданий на дату. Слежения за машинами нет.
// Живая карта рисуется скриптом 2ГИС и работает только с ключом (TWOGIS_KEY на сервере). Без ключа остаются ссылки «Открыть в 2ГИС».
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, ExternalLink, MapPin, Search } from "lucide-react";
import { get, post, errorText } from "../api";
import { useAuth } from "../auth";
import { fmtDateLong, plural, todayIso } from "../format";
import { useEntityParam } from "../hooks";
import { loadMapgl, twogisSearch } from "../maps";
import { Alert, Badge, Button, Card, Empty, PageHeader, QueryState } from "../ui";

interface Config { enabled: boolean; key: string; center: [number, number]; zoom: number; city: string }
interface Found { address: string; status: "ok" | "not_found" | "error"; lon?: number; lat?: number; title?: string; message?: string }
interface JobLite { id: number; time_from: string; plate: string; customer: string; address: string; state: string; state_label: string }
interface Pin { id: string; lon: number; lat: number; label: string }

const shift = (iso: string, days: number) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + days); return d.toISOString().slice(0, 10); };
const norm = (s: string) => s.trim().split(/\s+/).join(" ").toLowerCase();
const LOAD_ERROR = "Не удалось загрузить карту 2ГИС. Проверьте интернет и ключ (TWOGIS_KEY); ссылки «Открыть в 2ГИС» работают и без карты.";

/** Живая карта. Метки пересоздаются при смене списка, камера уходит на выбранную точку или охватывает все метки. */
function TwoGisMap({ config, pins, focus, onFail }: { config: Config; pins: Pin[]; focus: Pin | null; onFail: (m: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<any>(null);
  const api = useRef<any>(null);
  const marks = useRef<any[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let dead = false;
    loadMapgl().then((mapgl) => {
      if (dead || !box.current) return;
      api.current = mapgl;
      map.current = new mapgl.Map(box.current, { center: config.center, zoom: config.zoom, key: config.key, zoomControl: "topRight" });
      setReady(true);
    }).catch(() => { if (!dead) onFail(LOAD_ERROR); });
    return () => {
      dead = true;
      marks.current.forEach((m) => { try { m.destroy(); } catch { /* уже убрана */ } });
      marks.current = [];
      try { map.current?.destroy(); } catch { /* карта уже закрыта */ }
      map.current = null;
      setReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.key]);

  useEffect(() => {
    if (!ready || !map.current) return;
    marks.current.forEach((m) => { try { m.destroy(); } catch { /* уже убрана */ } });
    marks.current = pins.map((p) => new api.current.Marker(map.current, { coordinates: [p.lon, p.lat], label: { text: p.label, fontSize: 12 } }));
    if (pins.length === 1) { map.current.setCenter([pins[0].lon, pins[0].lat]); map.current.setZoom(15); }
    else if (pins.length > 1) {
      const lons = pins.map((p) => p.lon), lats = pins.map((p) => p.lat);
      try {
        map.current.fitBounds({ southWest: [Math.min(...lons), Math.min(...lats)], northEast: [Math.max(...lons), Math.max(...lats)] }, { padding: { top: 60, right: 60, bottom: 60, left: 60 } });
      } catch { map.current.setCenter([(Math.min(...lons) + Math.max(...lons)) / 2, (Math.min(...lats) + Math.max(...lats)) / 2]); }
    }
  }, [ready, pins]);

  useEffect(() => {
    if (!ready || !map.current || !focus) return;
    map.current.setCenter([focus.lon, focus.lat]);
    map.current.setZoom(16);
  }, [ready, focus]);

  return <div ref={box} className="map-canvas" data-testid="map-canvas" />;
}

/** Как получить ключ. Нужна только администратору: ключ вписывается один раз в настройки программы. */
function NoKey() {
  return (
    <Alert tone="info">
      <strong>Живая карта 2ГИС пока не подключена.</strong> Для неё нужен ключ доступа от 2ГИС, и его получает только владелец программы:
      <ol className="map-steps">
        <li>Откройте <a href="https://platform.2gis.ru" target="_blank" rel="noreferrer">platform.2gis.ru</a> и войдите (или зарегистрируйтесь).</li>
        <li>В «Менеджере платформы» создайте демо-ключ (он бесплатный и действует месяц; постоянный ключ платный, цену смотрите в тарифах 2ГИС).</li>
        <li>Впишите ключ в файл <code>.env</code> в папке программы после <code>TWOGIS_KEY=</code> и перезапустите программу.</li>
      </ol>
      Пока ключа нет, адреса ниже открываются в 2ГИС в новой вкладке.
    </Alert>
  );
}

export function MapPage() {
  const { can } = useAuth();
  const entity = useEntityParam();
  const [sp, setSp] = useSearchParams();
  const board = can("jobs.view");
  const date = board ? sp.get("date") || todayIso() : todayIso();
  const setDate = (d: string) => setSp((old) => { const n = new URLSearchParams(old); d && d !== todayIso() ? n.set("date", d) : n.delete("date"); return n; }, { replace: true });

  const cfg = useQuery({ queryKey: ["map-config"], queryFn: () => get<Config>("/map/config"), staleTime: 60_000 });
  const jobsQ = useQuery({
    queryKey: ["map-jobs", board, date, entity],
    queryFn: async () => (board ? (await get<{ items: JobLite[] }>("/jobs/day", { date, legal_entity_id: entity })).items : (await get<{ items: JobLite[] }>("/jobs/mine")).items),
    enabled: board || can("jobs.view_own"),
  });
  const jobs = useMemo(() => (jobsQ.data ?? []).filter((j) => j.address && j.state !== "cancelled"), [jobsQ.data]);
  const addresses = useMemo(() => Array.from(new Set(jobs.map((j) => j.address.trim()))).slice(0, 30), [jobs]);
  const enabled = !!cfg.data?.enabled;

  const geo = useQuery({
    queryKey: ["map-geocode", addresses],
    queryFn: () => post<{ items: Found[] }>("/map/geocode", { addresses }),
    enabled: enabled && addresses.length > 0,
    staleTime: Infinity,
  });
  const byAddress = useMemo(() => {
    const m = new Map<string, Found>();
    (geo.data?.items ?? []).forEach((f) => m.set(norm(f.address), f));
    return m;
  }, [geo.data]);
  const pointOf = (address: string) => { const f = byAddress.get(norm(address)); return f?.status === "ok" ? f : null; };

  const [query, setQuery] = useState("");
  const [hit, setHit] = useState<Pin | null>(null);
  const [searchNote, setSearchNote] = useState("");
  const [focus, setFocus] = useState<Pin | null>(null);
  const [mapError, setMapError] = useState("");
  const search = useMutation({
    mutationFn: (q: string) => post<{ items: Found[] }>("/map/geocode", { addresses: [q] }),
    onSuccess: (r, q) => {
      const f = r.items[0];
      if (f?.status === "ok" && f.lon !== undefined && f.lat !== undefined) {
        const pin = { id: "search", lon: f.lon, lat: f.lat, label: f.title || q };
        setHit(pin); setFocus(pin); setSearchNote(f.title || "");
      } else { setHit(null); setSearchNote(f?.status === "error" ? f.message || "2ГИС не отвечает" : "Такой адрес 2ГИС не нашёл. Уточните улицу и номер дома."); }
    },
    onError: (e) => { setHit(null); setSearchNote(errorText(e)); },
  });

  const pins: Pin[] = useMemo(() => {
    const out: Pin[] = [];
    for (const j of jobs) {
      const f = pointOf(j.address);
      if (f && f.lon !== undefined && f.lat !== undefined) out.push({ id: `job${j.id}`, lon: f.lon, lat: f.lat, label: `${j.time_from ? j.time_from.slice(0, 5) + " " : ""}${j.plate}` });
    }
    if (hit) out.push(hit);
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs, byAddress, hit]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    if (!enabled) { window.open(twogisSearch(q), "_blank", "noopener"); return; }
    search.mutate(q);
  };

  return (
    <>
      <PageHeader title="Карта" sub="Поиск адреса и адреса заданий на карте 2ГИС. Слежения за машинами нет" />
      <QueryState q={cfg}>
        {cfg.data && (
          <div className="stack">
            {!enabled && <NoKey />}
            {mapError && <Alert tone="error">{mapError}</Alert>}
            <div className="map-layout">
              <div className="stack map-side">
                <form className="map-search" onSubmit={submit} role="search">
                  <input className="input" aria-label="Адрес или место" placeholder="Адрес или место" value={query} onChange={(e) => setQuery(e.target.value)} />
                  <Button variant="primary" icon={Search} type="submit" loading={search.isPending}>Найти</Button>
                </form>
                {searchNote && (
                  <div className="muted small" data-testid="map-note">
                    {searchNote}{hit && <> · <a href={twogisSearch(query)} target="_blank" rel="noreferrer">Открыть в 2ГИС</a></>}
                  </div>
                )}
                {(board || can("jobs.view_own")) && (
                  <Card title={board ? "Технические задания" : "Мои задания сегодня"}>
                    {board && (
                      <div className="toolbar" style={{ marginBottom: 10 }}>
                        <Button icon={ChevronLeft} aria-label="Предыдущий день" onClick={() => setDate(shift(date, -1))} />
                        <input className="input compact" type="date" aria-label="Дата" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
                        <Button icon={ChevronRight} aria-label="Следующий день" onClick={() => setDate(shift(date, 1))} />
                      </div>
                    )}
                    <div className="muted small" style={{ marginBottom: 8 }}>{fmtDateLong(date)}</div>
                    <QueryState q={jobsQ}>
                      {jobs.length === 0 ? <Empty icon={MapPin} title="Заданий с адресом нет" /> : (
                        <ul className="map-jobs" aria-label="Адреса заданий">
                          {jobs.map((j) => {
                            const f = pointOf(j.address);
                            const seen = byAddress.get(norm(j.address));
                            return (
                              <li key={j.id}>
                                <div className="row" style={{ gap: 8, justifyContent: "space-between" }}>
                                  <strong>{j.time_from ? j.time_from.slice(0, 5) : "—"} · {j.plate}</strong>
                                  <Badge tone={j.state === "done" ? "success" : j.state === "on_site" ? "info" : "neutral"}>{j.state_label}</Badge>
                                </div>
                                {j.customer && <div className="small">{j.customer}</div>}
                                <div className="small muted">{j.address}</div>
                                <div className="row small" style={{ gap: 10, marginTop: 4 }}>
                                  {f && f.lon !== undefined && f.lat !== undefined && (
                                    <button type="button" className="linklike" onClick={() => setFocus({ id: `job${j.id}`, lon: f.lon!, lat: f.lat!, label: j.plate })}>Показать на карте</button>
                                  )}
                                  {enabled && seen && seen.status !== "ok" && <span className="muted">{seen.status === "not_found" ? "адрес не найден" : "2ГИС не ответил"}</span>}
                                  <a href={twogisSearch(j.address)} target="_blank" rel="noreferrer" className="row" style={{ gap: 3 }}>Открыть в 2ГИС <ExternalLink size={12} aria-hidden /></a>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </QueryState>
                    {enabled && jobs.length > 0 && <div className="muted small" style={{ marginTop: 8 }}>На карте {pins.filter((p) => p.id !== "search").length} из {jobs.length} {plural(jobs.length, "адреса", "адресов", "адресов")}</div>}
                  </Card>
                )}
              </div>
              {enabled ? <TwoGisMap config={cfg.data} pins={pins} focus={focus} onFail={setMapError} /> : (
                <div className="map-canvas map-off" data-testid="map-off"><MapPin size={40} aria-hidden /><span>Здесь будет карта 2ГИС</span></div>
              )}
            </div>
          </div>
        )}
      </QueryState>
    </>
  );
}
