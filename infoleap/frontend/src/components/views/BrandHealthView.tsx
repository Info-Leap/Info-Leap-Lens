/**
 * BrandHealthView — InfoLeap Pulse Brand Intelligence Dashboard
 *
 * Fixes vs v1:
 * - No infinite render loop: initial brand selection separated from filter-driven refetch
 * - API offline handled gracefully with clear instructions
 * - FunnelChart uses recharts v3 compatible API
 * - Proper loading/error boundaries per section
 */

import { useState, useEffect, useRef } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis,
  PolarRadiusAxis, Radar, ScatterChart, Scatter, Cell,
  FunnelChart, Funnel, LabelList,
} from "recharts";
import {
  BarChart3, RefreshCw, GitCompare, ChevronDown, ChevronUp,
  Info, AlertCircle, Server,
} from "lucide-react";
import { cn } from "../../lib/utils";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

const BRAND_COLORS = ["#1a5d4d", "#0ea5e9", "#f59e0b", "#8b5cf6", "#ec4899"];
const NPS_AVG = 45;

// ── Types ─────────────────────────────────────────────────────────────────────

type BrandData = {
  brand_name: string;
  tom_pct: number;
  spont_pct: number;
  aided_pct: number;
  nps: number | null;
  nps_base: number;
  nps_promoters_pct: number | null;
  nps_passives_pct: number | null;
  nps_detractors_pct: number | null;
  strat_score: number;
  aided: number;
};

type SegmentData = { tom_pct: number; spont_pct: number; aided_pct: number; base_n: number };
type FunnelCompareData = Record<string, Record<string, SegmentData>>;
type CorrelationData  = { brands: string[]; matrix: number[][]; base_n: number };
type ZoneMatrixData   = { brands: string[]; zones: string[]; tom_matrix: number[][]; nps_matrix: number[][] };

// ── Helpers ───────────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    headers: { "ngrok-skip-browser-warning": "1" },
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

function npsColor(nps: number | null) {
  if (nps === null) return "#9ca3af";
  return nps >= NPS_AVG ? "#22c55e" : nps >= 0 ? "#f59e0b" : "#ef4444";
}

function normNps(v: number | null) {
  return v !== null ? (v + 100) / 2 : 50;
}

// ── UI primitives ─────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub, accent }: {
  label: string; value: string; sub?: string; accent: string;
}) {
  return (
    <div
      className="bg-surface-container-lowest rounded-xl p-4 border border-outline-variant/30 shadow-sm"
      style={{ borderTop: `3px solid ${accent}` }}
    >
      <div className="text-[10px] font-bold uppercase tracking-widest text-secondary mb-1">{label}</div>
      <div className="text-2xl font-black text-on-surface leading-tight">{value}</div>
      {sub && <div className="text-[11px] text-secondary mt-1">{sub}</div>}
    </div>
  );
}

function SectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="border-l-4 border-primary pl-3 mt-8 mb-4">
      <h2 className="text-[11px] font-black text-primary uppercase tracking-widest">{title}</h2>
      {subtitle && <p className="text-[11px] text-secondary mt-0.5">{subtitle}</p>}
    </div>
  );
}

function Spinner({ text = "Loading…" }: { text?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 justify-center text-secondary text-sm">
      <RefreshCw className="w-4 h-4 animate-spin" />
      {text}
    </div>
  );
}

function ErrorBanner({ msg }: { msg: string }) {
  return (
    <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700 my-2">
      <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
      {msg}
    </div>
  );
}

// ── Single brand funnel (recharts) ────────────────────────────────────────────

function SingleFunnel({ brand }: { brand: BrandData }) {
  const data = [
    { name: "Aided Awareness", value: brand.aided_pct, fill: "#86efac" },
    { name: "Spontaneous",     value: brand.spont_pct, fill: "#30a76a" },
    { name: "Top of Mind",     value: brand.tom_pct,   fill: "#1a5d4d" },
  ];
  return (
    <ResponsiveContainer width="100%" height={300}>
      <FunnelChart>
        <Tooltip formatter={(v: number) => `${v}%`} />
        <Funnel dataKey="value" data={data}>
          <LabelList
            position="center"
            content={({ x, y, width, height, value, index }) => {
              const stage = data[index as number];
              if (!stage || (height as number) < 16) return null;
              return (
                <text x={Number(x) + Number(width) / 2} y={Number(y) + Number(height) / 2}
                  textAnchor="middle" dominantBaseline="middle"
                  fill="#fff" fontSize={11} fontWeight={700}>
                  {stage.name}: {String(value)}%
                </text>
              );
            }}
          />
          {data.map((d, i) => <Cell key={i} fill={d.fill} />)}
        </Funnel>
      </FunnelChart>
    </ResponsiveContainer>
  );
}

// ── Side-by-side mini funnels for comparison ──────────────────────────────────

function MiniFunnel({ brand, segLabel, data, color }: {
  brand: string; segLabel: string; data: SegmentData; color: string;
}) {
  const hex = color.replace("#", "");
  const [r, g, b] = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16));
  const funnelData = [
    { name: "Aided", value: data.aided_pct, fill: `rgba(${r},${g},${b},0.35)` },
    { name: "Spont", value: data.spont_pct, fill: `rgba(${r},${g},${b},0.65)` },
    { name: "TOM",   value: data.tom_pct,   fill: `rgba(${r},${g},${b},1)` },
  ];

  return (
    <div className="flex flex-col items-center">
      <div className="text-[10px] font-bold text-center mb-1" style={{ color }}>
        {brand}
      </div>
      {segLabel !== "Overall" && (
        <div className="text-[9px] text-secondary mb-1">{segLabel}</div>
      )}
      <ResponsiveContainer width="100%" height={200}>
        <FunnelChart>
          <Tooltip formatter={(v: number) => `${v}%`} />
          <Funnel dataKey="value" data={funnelData}>
            <LabelList
              position="center"
              content={({ x, y, width, height, index }) => {
                const d = funnelData[index as number];
                if (!d || (height as number) < 14) return null;
                return (
                  <text x={Number(x) + Number(width) / 2} y={Number(y) + Number(height) / 2}
                    textAnchor="middle" dominantBaseline="middle"
                    fill="#fff" fontSize={9} fontWeight={700}>
                    {d.value}%
                  </text>
                );
              }}
            />
            {funnelData.map((d, i) => <Cell key={i} fill={d.fill} />)}
          </Funnel>
        </FunnelChart>
      </ResponsiveContainer>
      <div className="text-[9px] text-secondary">n={data.base_n.toLocaleString()}</div>
    </div>
  );
}

function ComparisonGrid({ data, brands }: {
  data: FunnelCompareData; brands: string[];
}) {
  const segments = Object.keys(data[brands[0]] ?? {});
  if (!segments.length) return <p className="text-secondary text-sm">No data</p>;

  return (
    <div className="space-y-6">
      {segments.map(seg => (
        <div key={seg}>
          {segments.length > 1 && (
            <div className="text-[11px] font-bold text-secondary uppercase tracking-wider mb-2 border-b border-outline-variant/20 pb-1">
              {seg}
            </div>
          )}
          <div
            className="grid gap-3"
            style={{ gridTemplateColumns: `repeat(${brands.length}, minmax(0, 1fr))` }}
          >
            {brands.map((brand, i) => (
              <MiniFunnel
                key={brand}
                brand={brand}
                segLabel={seg}
                data={data[brand]?.[seg] ?? { tom_pct: 0, spont_pct: 0, aided_pct: 0, base_n: 0 }}
                color={BRAND_COLORS[i % BRAND_COLORS.length]}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Multi-brand radar ─────────────────────────────────────────────────────────

function MultiRadar({ brands }: { brands: BrandData[] }) {
  const axes = ["Salience (TOM)", "Recall (Spont)", "Total Reach", "Loyalty (NPS)", "Rater Depth"];
  const chartData = axes.map((axis, i) => {
    const entry: Record<string, string | number> = { axis };
    brands.forEach(b => {
      const vals = [
        b.tom_pct, b.spont_pct, b.aided_pct,
        normNps(b.nps),
        Math.min((b.nps_base / 6631) * 500, 100),
      ];
      entry[b.brand_name] = Math.round(vals[i] * 10) / 10;
    });
    return entry;
  });
  return (
    <ResponsiveContainer width="100%" height={400}>
      <RadarChart data={chartData} margin={{ top: 20, right: 40, bottom: 20, left: 40 }}>
        <PolarGrid stroke="#e5e7eb" />
        <PolarAngleAxis dataKey="axis" tick={{ fontSize: 10, fill: "#6b7280" }} />
        <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 8 }} />
        {brands.map((b, i) => (
          <Radar key={b.brand_name} name={b.brand_name} dataKey={b.brand_name}
            stroke={BRAND_COLORS[i % BRAND_COLORS.length]}
            fill={BRAND_COLORS[i % BRAND_COLORS.length]}
            fillOpacity={0.12} strokeWidth={2} />
        ))}
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Tooltip formatter={(v: number) => v.toFixed(1)} />
      </RadarChart>
    </ResponsiveContainer>
  );
}

// ── Co-awareness heatmap (SVG) ────────────────────────────────────────────────

function CorrelHeatmap({ data }: { data: CorrelationData }) {
  const { brands, matrix } = data;
  const n = brands.length;
  const CELL = 38;
  const LABEL = 88;

  const maxOff = Math.max(...matrix.flatMap((row, i) => row.filter((_, j) => i !== j)));
  const minOff = Math.min(...matrix.flatMap((row, i) => row.filter((_, j) => i !== j)));

  function cellBg(v: number, diag: boolean) {
    if (diag) return "#1a5d4d";
    const t = Math.max(0, Math.min(1, (v - minOff) / (maxOff - minOff + 0.01)));
    return `rgba(26,93,77,${0.08 + t * 0.72})`;
  }
  function cellText(v: number, diag: boolean) {
    return diag || v > maxOff * 0.55 ? "#fff" : "#374151";
  }

  return (
    <div className="overflow-x-auto overflow-y-auto max-h-[520px]">
      <svg width={LABEL + n * CELL + 20} height={LABEL + n * CELL + 10} fontSize={8}>
        {brands.map((b, j) => (
          <text key={j}
            x={LABEL + j * CELL + CELL / 2} y={LABEL - 5}
            transform={`rotate(-40,${LABEL + j * CELL + CELL / 2},${LABEL - 5})`}
            textAnchor="start" fill="#6b7280">
            {b.length > 11 ? b.slice(0, 10) + "…" : b}
          </text>
        ))}
        {brands.map((brand, i) => (
          <g key={i}>
            <text x={LABEL - 4} y={LABEL + i * CELL + CELL / 2 + 3}
              textAnchor="end" fill="#6b7280">
              {brand.length > 11 ? brand.slice(0, 10) + "…" : brand}
            </text>
            {matrix[i].map((v, j) => {
              const diag = i === j;
              return (
                <g key={j}>
                  <rect x={LABEL + j * CELL} y={LABEL + i * CELL}
                    width={CELL} height={CELL}
                    fill={cellBg(v, diag)} stroke="#fff" strokeWidth={1} rx={1} />
                  <text x={LABEL + j * CELL + CELL / 2} y={LABEL + i * CELL + CELL / 2 + 3}
                    textAnchor="middle" fill={cellText(v, diag)} fontWeight={diag ? 700 : 400}>
                    {v.toFixed(0)}%
                  </text>
                </g>
              );
            })}
          </g>
        ))}
      </svg>
    </div>
  );
}

// ── Positioning map (client-side PCA) ─────────────────────────────────────────

function pcaProject(zoneMatrix: ZoneMatrixData) {
  const { brands, tom_matrix, nps_matrix } = zoneMatrix;
  const X = tom_matrix.map((row, i) => [...row, ...nps_matrix[i]]);
  const n = X.length, p = X[0].length;
  const means = Array(p).fill(0) as number[];
  const stds  = Array(p).fill(0) as number[];
  X.forEach(row => row.forEach((v, j) => { means[j] += v / n; }));
  X.forEach(row => row.forEach((v, j) => { stds[j]  += (v - means[j]) ** 2 / n; }));
  stds.forEach((_, j) => { stds[j] = Math.sqrt(stds[j]) || 1; });
  const Xs = X.map(row => row.map((v, j) => (v - means[j]) / stds[j]));

  // Power iteration for PC1
  let v1 = Array(p).fill(1 / Math.sqrt(p)) as number[];
  for (let it = 0; it < 40; it++) {
    const Av = Array(p).fill(0) as number[];
    Xs.forEach(row => row.forEach((x, j) => { row.forEach((y, k) => { Av[j] += x * y * v1[k] / n; }); }));
    // Correct: covariance matrix times v1
    const cov_v1 = Array(p).fill(0) as number[];
    for (let j = 0; j < p; j++) for (let k = 0; k < p; k++) {
      cov_v1[j] += Xs.reduce((s, row) => s + row[j] * row[k], 0) / n * v1[k];
    }
    const norm = Math.sqrt(cov_v1.reduce((s, x) => s + x * x, 0)) || 1;
    v1 = cov_v1.map(x => x / norm);
  }

  // PC2 orthogonal to v1
  let v2 = [...v1].reverse();
  const dot = v1.reduce((s, x, j) => s + x * v2[j], 0);
  v2 = v2.map((x, j) => x - dot * v1[j]);
  const n2 = Math.sqrt(v2.reduce((s, x) => s + x * x, 0)) || 1;
  v2 = v2.map(x => x / n2);

  return brands.map((name, i) => ({
    name,
    x: Math.round(Xs[i].reduce((s, x, j) => s + x * v1[j], 0) * 100) / 100,
    y: Math.round(Xs[i].reduce((s, x, j) => s + x * v2[j], 0) * 100) / 100,
  }));
}

function PositioningMap({ zoneMatrix, highlight }: {
  zoneMatrix: ZoneMatrixData; highlight?: string;
}) {
  const points = pcaProject(zoneMatrix);

  return (
    <ResponsiveContainer width="100%" height={440}>
      <ScatterChart margin={{ top: 30, right: 40, bottom: 50, left: 40 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
        <XAxis dataKey="x" type="number" name="Dim 1" tick={{ fontSize: 10 }}
          label={{ value: "Dim 1 — Geographic Reach Pattern", position: "insideBottom", offset: -15, fontSize: 11 }} />
        <YAxis dataKey="y" type="number" name="Dim 2" tick={{ fontSize: 10 }}
          label={{ value: "Dim 2", angle: -90, position: "insideLeft", fontSize: 11 }} />
        <Tooltip
          content={({ payload }) => {
            const p = payload?.[0]?.payload as { name?: string; x?: number; y?: number } | undefined;
            if (!p?.name) return null;
            return (
              <div className="bg-white border border-outline-variant/30 rounded-lg px-3 py-2 shadow text-xs">
                <div className="font-bold">{p.name}</div>
                <div className="text-secondary">Dim1: {p.x} · Dim2: {p.y}</div>
              </div>
            );
          }}
        />
        <Scatter data={points} name="brands">
          {points.map((p, i) => (
            <Cell key={i}
              fill={p.name === highlight ? "#1a5d4d" : "#86efac"}
              stroke={p.name === highlight ? "#0a2e22" : "#30a76a"}
              strokeWidth={p.name === highlight ? 2 : 1}
            />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}

// ── NPS League ────────────────────────────────────────────────────────────────

function NpsLeague({ brands, highlight }: { brands: BrandData[]; highlight: string }) {
  const eligible = [...brands]
    .filter(b => b.nps !== null && b.nps_base >= 30)
    .sort((a, b) => (b.nps ?? 0) - (a.nps ?? 0))
    .slice(0, 15);

  const data = eligible.map(b => ({
    name: b.brand_name,
    nps:  b.nps ?? 0,
    fill: b.brand_name === highlight ? "#1a5d4d"
          : (b.nps ?? 0) >= NPS_AVG ? "#22c55e" : "#f59e0b",
  }));

  return (
    <ResponsiveContainer width="100%" height={Math.max(360, eligible.length * 30)}>
      <BarChart data={data} layout="vertical" margin={{ top: 5, right: 60, left: 5, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f3f4f6" />
        <XAxis type="number" domain={["dataMin - 10", "dataMax + 20"]}
          tickFormatter={v => `${v > 0 ? "+" : ""}${v}`} tick={{ fontSize: 10 }} />
        <YAxis dataKey="name" type="category" tick={{ fontSize: 10 }} width={75} />
        <Tooltip formatter={(v: number) => `${v > 0 ? "+" : ""}${v.toFixed(0)}`} />
        <Bar dataKey="nps" radius={[0, 4, 4, 0]}
          label={{ position: "right", formatter: (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(0)}`, fontSize: 10 }}>
          {data.map((d, i) => <Cell key={i} fill={d.fill} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Awareness landscape ───────────────────────────────────────────────────────

function AwarenessLandscape({ brands }: { brands: BrandData[] }) {
  const top = [...brands].sort((a, b) => b.aided_pct - a.aided_pct).slice(0, 15).reverse();
  const data = top.map(b => ({
    name: b.brand_name,
    TOM:  b.tom_pct,
    Spont: Math.max(0, b.spont_pct - b.tom_pct),
    Aided: Math.max(0, b.aided_pct - b.spont_pct),
  }));

  return (
    <ResponsiveContainer width="100%" height={Math.max(350, top.length * 26)}>
      <BarChart data={data} layout="vertical" margin={{ top: 5, right: 20, left: 5, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f3f4f6" />
        <XAxis type="number" domain={[0, 100]} tickFormatter={v => `${v}%`} tick={{ fontSize: 10 }} />
        <YAxis dataKey="name" type="category" tick={{ fontSize: 10 }} width={75} />
        <Tooltip formatter={(v: number) => `${v.toFixed(1)}%`} />
        <Legend wrapperStyle={{ fontSize: 10 }} />
        <Bar dataKey="TOM"   stackId="a" name="Top of Mind" fill="#1a5d4d" />
        <Bar dataKey="Spont" stackId="a" name="Spontaneous only" fill="#30a76a" />
        <Bar dataKey="Aided" stackId="a" name="Aided only" fill="#86efac" />
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Main View ─────────────────────────────────────────────────────────────────

const SEG_OPTIONS: Record<string, string[]> = {
  overall:  [],
  zone:     ["North", "South", "East", "West"],
  gender:   ["Male", "Female"],
  age_band: ["25-35", "36-50"],
  city:     ["Delhi", "Mumbai", "Bangalore", "Chennai", "Hyderabad", "Kolkata",
             "Ahmedabad", "Lucknow", "Patna", "Bhubaneshwar", "Nagaon", "Bikaner",
             "Patiala", "Cochin", "Guntur", "Hassan", "Kolhapur", "Ujjain"],
};

type Filters = { zone: string; gender: string; age_band: string };

export function BrandHealthView() {
  // ── Core data ───────────────────────────────────────────────────────────────
  const [brands, setBrands]         = useState<BrandData[]>([]);
  const [baseN, setBaseN]           = useState(0);
  const [selBrand, setSelBrand]     = useState<string | null>(null);
  const [loadingMain, setLoadingMain] = useState(true);
  const [mainError, setMainError]   = useState<string | null>(null);

  // ── Imagery data (one-time) ─────────────────────────────────────────────────
  const [corrData, setCorrData]     = useState<CorrelationData | null>(null);
  const [zoneMatrix, setZoneMatrix] = useState<ZoneMatrixData | null>(null);
  const [loadingImagery, setLoadingImagery] = useState(true);

  // ── Comparison ──────────────────────────────────────────────────────────────
  const [showCompare, setShowCompare] = useState(false);
  const [cmpBrands, setCmpBrands]   = useState<string[]>([]);
  const [segType, setSegType]       = useState("overall");
  const [segVals, setSegVals]       = useState<string[]>([]);
  const [cmpData, setCmpData]       = useState<FunnelCompareData | null>(null);
  const [loadingCmp, setLoadingCmp] = useState(false);

  // ── Filters ─────────────────────────────────────────────────────────────────
  const [filters, setFilters]       = useState<Filters>({ zone: "all", gender: "all", age_band: "all" });

  // ── Radar brand picker ───────────────────────────────────────────────────────
  const [radarPicks, setRadarPicks] = useState<string[]>([]);
  const [showImagery, setShowImagery] = useState(true);

  const initializedRef = useRef(false);

  // ── Fetch main brand health data ────────────────────────────────────────────
  useEffect(() => {
    setLoadingMain(true);
    setMainError(null);
    const params = new URLSearchParams({
      zone:     filters.zone,
      gender:   filters.gender,
      age_band: filters.age_band,
    });
    apiFetch<{ status: string; base_n: number; brands: BrandData[] }>(`/api/brand-health?${params}`)
      .then(d => {
        if (d.status === "success") {
          setBrands(d.brands);
          setBaseN(d.base_n);
          // Set default brand only on first load
          if (!initializedRef.current && d.brands.length > 0) {
            setSelBrand(d.brands[0].brand_name);
            setRadarPicks(d.brands.slice(0, 3).map(b => b.brand_name));
            initializedRef.current = true;
          }
        }
      })
      .catch(e => setMainError(e.message))
      .finally(() => setLoadingMain(false));
  }, [filters]);

  // ── Fetch imagery data (one-time) ────────────────────────────────────────────
  useEffect(() => {
    setLoadingImagery(true);
    Promise.allSettled([
      apiFetch<{ data: CorrelationData }>("/api/brand-imagery/correlation?top_n=12"),
      apiFetch<{ data: ZoneMatrixData }>("/api/brand-imagery/zone-matrix?top_n=20"),
    ]).then(([corr, zm]) => {
      if (corr.status === "fulfilled") setCorrData(corr.value.data);
      if (zm.status   === "fulfilled") setZoneMatrix(zm.value.data);
    }).finally(() => setLoadingImagery(false));
  }, []);

  // ── Fetch comparison funnel ──────────────────────────────────────────────────
  useEffect(() => {
    if (!showCompare || !selBrand || cmpBrands.length === 0) {
      setCmpData(null);
      return;
    }
    setLoadingCmp(true);
    const allBrands = [selBrand, ...cmpBrands];
    const params = new URLSearchParams({
      brands:         allBrands.join(","),
      segment_type:   segType,
      segment_values: segVals.join(","),
    });
    apiFetch<{ data: FunnelCompareData }>(`/api/funnel-compare?${params}`)
      .then(d => setCmpData(d.data))
      .catch(() => setCmpData(null))
      .finally(() => setLoadingCmp(false));
  }, [showCompare, selBrand, cmpBrands, segType, segVals]);

  // ── Derived ─────────────────────────────────────────────────────────────────
  const brandData  = brands.find(b => b.brand_name === selBrand) ?? null;
  const otherBrands = brands.filter(b => b.brand_name !== selBrand).map(b => b.brand_name);
  const radarData  = brands.filter(b => radarPicks.includes(b.brand_name));
  const allNames   = brands.map(b => b.brand_name);
  const allCmpBrands = selBrand ? [selBrand, ...cmpBrands] : cmpBrands;

  // ── Offline / error state ───────────────────────────────────────────────────
  if (!loadingMain && mainError) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="max-w-md text-center">
          <Server className="w-10 h-10 text-red-400 mx-auto mb-3" />
          <h3 className="font-bold text-on-surface mb-2">API Server Not Reachable</h3>
          <p className="text-secondary text-sm mb-4">
            The Brand Health API server must be running. Start it with:
          </p>
          <pre className="bg-surface-container text-on-surface text-xs p-3 rounded-lg text-left">
            uvicorn oxdata.api_server:app{"\n"}  --host 0.0.0.0 --port 8001 --reload
          </pre>
          <p className="text-secondary text-xs mt-3">Error: {mainError}</p>
        </div>
      </div>
    );
  }

  if (loadingMain) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <BarChart3 className="w-10 h-10 text-primary animate-pulse mx-auto mb-3" />
          <p className="text-secondary text-sm">Loading brand intelligence…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden">

      {/* ── Filter bar ─────────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 bg-surface-container border-b border-outline-variant/20 px-5 py-2.5 flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-primary" />
          <span className="font-bold text-sm text-on-surface">Brand Health</span>
          <span className="text-secondary text-xs">· {baseN.toLocaleString()} respondents</span>
        </div>

        <div className="flex items-center gap-2 ml-auto flex-wrap">
          {/* Deep-dive brand */}
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-secondary uppercase font-bold">Brand</span>
            <select value={selBrand ?? ""} onChange={e => setSelBrand(e.target.value)}
              className="text-xs border border-outline-variant/40 rounded-lg px-2 py-1.5 bg-surface-container-lowest focus:outline-none focus:ring-1 focus:ring-primary max-w-[140px]">
              {allNames.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>

          {(["zone", "gender", "age_band"] as const).map(key => (
            <select key={key} value={filters[key]}
              onChange={e => setFilters(f => ({ ...f, [key]: e.target.value }))}
              className="text-xs border border-outline-variant/40 rounded-lg px-2 py-1.5 bg-surface-container-lowest focus:outline-none focus:ring-1 focus:ring-primary">
              <option value="all">All {key === "age_band" ? "Ages" : key.charAt(0).toUpperCase() + key.slice(1) + "s"}</option>
              {(key === "zone" ? ["North","South","East","West"]
                : key === "gender" ? ["Male","Female"]
                : ["25-35","36-50"]).map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          ))}

          <button onClick={() => setFilters({ zone: "all", gender: "all", age_band: "all" })}
            className="text-[10px] text-secondary hover:text-primary px-2 py-1.5 rounded-lg hover:bg-surface-variant transition-colors">
            Reset
          </button>
        </div>
      </div>

      {/* ── Main content ───────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-5 py-4">

        {/* KPI Cards */}
        {brandData && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <KpiCard label="👁 Aided Awareness" value={`${brandData.aided_pct}%`}
              sub={`${brandData.aided.toLocaleString()} of ${baseN.toLocaleString()} respondents`}
              accent="#1a5d4d" />
            <KpiCard label="🧠 Top of Mind" value={`${brandData.tom_pct}%`}
              sub={`#${brands.findIndex(b => b.brand_name === selBrand) + 1} by aided reach`}
              accent="#0ea5e9" />
            <KpiCard
              label="⭐ NPS Score"
              value={brandData.nps !== null ? `${brandData.nps >= 0 ? "+" : ""}${brandData.nps.toFixed(0)}` : "N/A"}
              sub={brandData.nps !== null ? `${brandData.nps >= NPS_AVG ? "▲ Above" : "▼ Below"} industry avg +${NPS_AVG}` : "Insufficient raters"}
              accent={npsColor(brandData.nps)} />
            <KpiCard label="📊 Funnel Depth" value={`${brandData.spont_pct}%`}
              sub={`${brandData.aided_pct}% → ${brandData.spont_pct}% → ${brandData.tom_pct}%`}
              accent="#f59e0b" />
          </div>
        )}

        {/* ── SECTION: Awareness Funnel ────────────────────────────────────── */}
        <SectionHeader
          title="📊 Awareness Funnel"
          subtitle={selBrand ? `${selBrand} — Aided → Spontaneous → Top of Mind` : ""}
        />

        {/* Inline controls */}
        <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 mb-4">
          <div className="flex items-center gap-4 flex-wrap">
            {/* Base brand badge */}
            <div>
              <div className="text-[9px] text-secondary uppercase font-bold mb-0.5">Base Brand</div>
              <div className="text-xs font-bold text-white bg-primary px-3 py-1 rounded-full">
                {selBrand ?? "—"}
              </div>
            </div>

            {/* Compare toggle */}
            <button
              onClick={() => setShowCompare(v => !v)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-all border",
                showCompare
                  ? "bg-primary text-on-primary border-primary"
                  : "border-outline-variant/40 text-secondary hover:text-primary bg-white"
              )}
            >
              <GitCompare className="w-3.5 h-3.5" />
              {showCompare ? "Compare: ON" : "⚖ Compare brands"}
            </button>

            {showCompare && (
              <>
                {/* Brand multiselect */}
                <div>
                  <div className="text-[9px] text-secondary uppercase font-bold mb-0.5">Compare with (max 4)</div>
                  <div className="flex flex-wrap gap-1">
                    {otherBrands.map(n => (
                      <button key={n}
                        onClick={() => setCmpBrands(prev =>
                          prev.includes(n) ? prev.filter(x => x !== n)
                            : prev.length < 4 ? [...prev, n] : prev
                        )}
                        className={cn(
                          "px-2 py-0.5 rounded-full text-[10px] font-medium border transition-all",
                          cmpBrands.includes(n)
                            ? "bg-primary/10 border-primary text-primary"
                            : "border-outline-variant/40 text-secondary hover:text-primary bg-white"
                        )}
                      >{n}</button>
                    ))}
                  </div>
                </div>

                {/* Segment type */}
                <div>
                  <div className="text-[9px] text-secondary uppercase font-bold mb-0.5">Segment by</div>
                  <div className="flex gap-1">
                    {["overall", "zone", "gender", "age_band", "city"].map(s => (
                      <button key={s}
                        onClick={() => { setSegType(s); setSegVals([]); }}
                        className={cn(
                          "px-2 py-0.5 rounded text-[10px] font-medium transition-all",
                          segType === s
                            ? "bg-primary text-on-primary"
                            : "bg-white border border-outline-variant/40 text-secondary hover:text-primary"
                        )}
                      >{s === "age_band" ? "Age" : s.charAt(0).toUpperCase() + s.slice(1)}</button>
                    ))}
                  </div>
                </div>

                {/* Segment values */}
                {segType !== "overall" && SEG_OPTIONS[segType]?.length > 0 && (
                  <div>
                    <div className="text-[9px] text-secondary uppercase font-bold mb-0.5">{segType} values</div>
                    <div className="flex flex-wrap gap-1 max-w-[240px]">
                      {SEG_OPTIONS[segType].map(v => (
                        <button key={v}
                          onClick={() => setSegVals(prev =>
                            prev.includes(v) ? prev.filter(x => x !== v) : [...prev, v]
                          )}
                          className={cn(
                            "px-2 py-0.5 rounded text-[10px] font-medium transition-all",
                            segVals.includes(v)
                              ? "bg-amber-500 text-white"
                              : "bg-white border border-outline-variant/40 text-secondary hover:text-primary"
                          )}
                        >{v}</button>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* Primary funnel — always visible */}
        {brandData && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div className="md:col-span-2 bg-surface-container-lowest rounded-xl p-4 border border-outline-variant/20">
              <div className="text-[11px] font-bold text-primary mb-2">
                ▌ {selBrand} — Awareness Funnel
              </div>
              <SingleFunnel brand={brandData} />
            </div>
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-xs text-amber-900 space-y-3">
              <div className="font-bold text-amber-800">📊 Funnel Insight</div>
              <div>
                <span className="font-semibold">Aided:</span> {brandData.aided_pct}% of {baseN.toLocaleString()} respondents recognise {selBrand} when prompted.
              </div>
              <div>
                <span className="font-semibold">Spontaneous:</span> {brandData.spont_pct}% recall {selBrand} without any prompt — indicating organic brand salience.
              </div>
              <div>
                <span className="font-semibold">Top of Mind:</span> {brandData.tom_pct}% name {selBrand} as the very first brand — the strongest form of awareness.
              </div>
              <div className="border-t border-amber-200 pt-2">
                <span className="font-semibold">Conversion:</span>{" "}
                {brandData.aided_pct > 0 ? ((brandData.tom_pct / brandData.aided_pct) * 100).toFixed(0) : 0}% of aided-aware respondents reach TOM status.
              </div>
            </div>
          </div>
        )}

        {/* Comparison grid */}
        {showCompare && cmpBrands.length > 0 && (
          <div className="bg-surface-container-lowest rounded-xl p-4 border border-primary/20 mb-6">
            <div className="text-[11px] font-bold text-primary mb-3">
              ⚖ Brand Comparison{segType !== "overall" ? ` — by ${segType}` : " — Overall"}
            </div>
            {loadingCmp ? <Spinner text="Computing comparison funnels…" /> :
             cmpData ? (
               <ComparisonGrid data={cmpData} brands={allCmpBrands} />
             ) : (
               <p className="text-secondary text-xs text-center py-4">Select brands above to compare</p>
             )}
          </div>
        )}

        {/* ── SECTION: Awareness Landscape ─────────────────────────────────── */}
        <SectionHeader title="🏆 Awareness Landscape"
          subtitle="Market-wide stacked awareness — TOM / Spontaneous / Aided breakdown" />
        <div className="bg-surface-container-lowest rounded-xl p-4 border border-outline-variant/20">
          <AwarenessLandscape brands={brands} />
        </div>

        {/* ── SECTION: NPS League ──────────────────────────────────────────── */}
        <SectionHeader title="🏅 NPS League"
          subtitle={`${selBrand ?? ""} ranked among ${brands.filter(b => b.nps !== null).length} brands`} />
        <div className="bg-surface-container-lowest rounded-xl p-4 border border-outline-variant/20">
          {selBrand && <NpsLeague brands={brands} highlight={selBrand} />}
        </div>

        {/* ══════════════════════════════════════════════════════════════════ */}
        {/* PART 2: BRAND IMAGERY                                             */}
        {/* ══════════════════════════════════════════════════════════════════ */}
        <div className="border-t-2 border-primary mt-10 pt-6">
          <div className="flex items-start justify-between mb-3">
            <div>
              <h2 className="text-xs font-black text-primary uppercase tracking-widest">
                PART 2 — Brand Imagery &amp; Positioning
              </h2>
              <p className="text-[11px] text-secondary mt-0.5">
                Multi-brand profiles, co-awareness heatmap, geographic positioning map.
              </p>
            </div>
            <button onClick={() => setShowImagery(v => !v)}
              className="p-1.5 rounded-lg hover:bg-surface-variant text-secondary">
              {showImagery ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
          </div>

          <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-[11px] text-amber-800 mb-4">
            <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
            Brand imagery attributes (BQ3) pending ingestion. Radar currently uses derived signals:
            TOM%, SPONT%, AIDED%, NPS-norm, Rater Depth.
          </div>

          {showImagery && (
            <>
              {/* Brand Image Profiling */}
              <SectionHeader title="🕸️ Brand Image Profiling"
                subtitle="Compare derived health profiles across multiple brands" />
              <div className="mb-3 flex flex-wrap gap-1.5">
                {allNames.map(n => (
                  <button key={n}
                    onClick={() => setRadarPicks(prev =>
                      prev.includes(n) ? prev.filter(x => x !== n)
                        : prev.length < 5 ? [...prev, n] : prev
                    )}
                    className={cn(
                      "px-2.5 py-1 rounded-full text-[10px] font-semibold border transition-all",
                      radarPicks.includes(n)
                        ? "bg-primary text-on-primary border-primary"
                        : "bg-surface-container border-outline-variant/40 text-secondary hover:text-primary"
                    )}
                  >{n}</button>
                ))}
                <span className="text-[10px] text-secondary self-center ml-1">up to 5</span>
              </div>
              <div className="bg-surface-container-lowest rounded-xl p-4 border border-outline-variant/20">
                {radarData.length > 0
                  ? <MultiRadar brands={radarData} />
                  : <p className="text-secondary text-sm text-center py-8">Select brands above</p>}
              </div>

              {/* Correlation heatmap */}
              <SectionHeader title="🔗 Brand Correlation Map"
                subtitle="% of respondents aware of BOTH brands. Diagonal = self-awareness." />
              <div className="bg-surface-container-lowest rounded-xl p-4 border border-outline-variant/20">
                {loadingImagery ? <Spinner text="Computing correlations…" /> :
                 corrData ? <CorrelHeatmap data={corrData} /> :
                 <ErrorBanner msg="Correlation data unavailable. Ensure API server is running." />}
              </div>

              {/* Positioning map */}
              <SectionHeader title="🗺️ Geographic Positioning Map"
                subtitle="Brands clustered by zone-awareness + NPS profile (PCA). Closer = similar geographic pattern." />
              <div className="bg-surface-container-lowest rounded-xl p-4 border border-outline-variant/20">
                {loadingImagery ? <Spinner text="Computing positioning…" /> :
                 zoneMatrix ? <PositioningMap zoneMatrix={zoneMatrix} highlight={selBrand ?? ""} /> :
                 <ErrorBanner msg="Zone matrix unavailable. Ensure API server is running." />}
              </div>
              <p className="text-[10px] text-secondary mt-2 px-1">
                Each point = a brand. Position derived from PCA on [North_TOM, South_TOM, East_TOM, West_TOM,
                North_NPS, South_NPS, East_NPS, West_NPS]. Star = selected brand.
              </p>
            </>
          )}
        </div>

        <div className="h-10" />
      </div>
    </div>
  );
}
