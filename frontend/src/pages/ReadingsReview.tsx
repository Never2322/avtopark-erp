import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { get } from "../api";
import { fmtDateTime, fmtNum, readingUnit } from "../format";
import { useEntityParam } from "../hooks";
import { Card, Empty, PageHeader, QueryState, Table } from "../ui";
import { ReviewButtons } from "./VehicleCard";

interface Pending {
  id: number; vehicle_id: number; plate: string; vehicle: string; kind: string; kind_label: string; value: number; previous: number | null;
  recorded_at: string; author: string | null; note: string; source_label: string;
}

export function ReadingsReview() {
  const entity = useEntityParam();
  const q = useQuery({ queryKey: ["readings-pending", entity ?? null], queryFn: () => get<{ items: Pending[] }>("/readings/pending", { legal_entity_id: entity }) });
  return (
    <>
      <PageHeader title="Показания на проверку" sub="Сюда попадают показания с необычно большим скачком: до решения они не влияют на пробег машины" />
      <Card flush>
        <QueryState q={q}>
          {q.data && (q.data.items.length === 0 ? <Empty icon={Gauge} title="Всё проверено">Показаний, которые ждут решения, нет.</Empty> : (
            <Table rows={q.data.items} rowKey={(r) => r.id}
              columns={[
                { key: "v", header: "Машина", render: (r) => <><Link to={`/vehicles/${r.vehicle_id}`}><strong>{r.plate}</strong></Link><span className="muted"> {r.vehicle}</span></> },
                { key: "kind", header: "Что", hideSm: true, render: (r) => r.kind_label },
                { key: "prev", header: "Было", num: true, render: (r) => (r.previous !== null ? `${fmtNum(r.previous)} ${readingUnit(r.kind)}` : "—") },
                { key: "val", header: "Ввели", num: true, render: (r) => <strong>{fmtNum(r.value)} {readingUnit(r.kind)}</strong> },
                { key: "jump", header: "Скачок", num: true, hideSm: true, render: (r) => (r.previous !== null ? `+${fmtNum(r.value - r.previous)}` : "—") },
                { key: "who", header: "Кто, когда", hideSm: true, render: (r) => <>{r.author ?? "—"}<div className="muted small">{fmtDateTime(r.recorded_at)} · {r.source_label}</div></> },
                { key: "act", header: "", render: (r) => <div className="row"><ReviewButtons r={r} /></div> },
              ]} />
          ))}
        </QueryState>
      </Card>
    </>
  );
}
