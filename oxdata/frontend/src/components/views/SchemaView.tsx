import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Database, Eye, ChevronDown, ChevronRight, RefreshCw, AlertCircle } from "lucide-react";
import { cn } from "../../lib/utils";

// ── Types ──────────────────────────────────────────────────────────────────────

interface ColInfo { name: string; type: string; pk: boolean }

interface SchemaData {
  counts:  Record<string, number>;
  columns: Record<string, ColInfo[]>;
  samples: Record<string, Record<string, unknown>[]>;
}

// ── Static descriptions ────────────────────────────────────────────────────────

const TABLE_META: Record<string, { desc: string; kind: "fact" | "dim"; icon: string }> = {
  fact_respondents:       { desc: "Core respondent row. All 6,631 interviews.",                         kind: "fact", icon: "👤" },
  fact_brand_awareness:   { desc: "TOM / SPONT / AIDED recall events per respondent × brand.",          kind: "fact", icon: "📢" },
  fact_brand_nps:         { desc: "NPS ratings 0-10 per respondent × brand (sparse).",                 kind: "fact", icon: "⭐" },
  fact_kitchen_ownership: { desc: "Kitchen appliance binary flags expanded to one row per item.",       kind: "fact", icon: "🍳" },
  fact_recent_purchase:   { desc: "Recent purchase selections ranked 1=most recent.",                   kind: "fact", icon: "🛒" },
  fact_room_appliances:   { desc: "Room appliance binary flags (fans, AC, bulbs, etc.).",              kind: "fact", icon: "🏠" },
  fact_verbatims:         { desc: "Open-ended text responses.",                                         kind: "fact", icon: "💬" },
  dim_brand:              { desc: "56 brand codes → names.",                                           kind: "dim",  icon: "🏷️" },
  dim_city:               { desc: "18 cities → zone mapping.",                                         kind: "dim",  icon: "🏙️" },
  dim_zone:               { desc: "4 zones (North/South/West/East).",                                  kind: "dim",  icon: "🗺️" },
  dim_kitchen_appliance:  { desc: "14 kitchen appliance types.",                                       kind: "dim",  icon: "🔧" },
  dim_room_appliance:     { desc: "17 room appliance types.",                                          kind: "dim",  icon: "💡" },
  dim_date:               { desc: "39 interview dates with year/month/quarter.",                        kind: "dim",  icon: "📅" },
};

const VIEW_META: Record<string, { desc: string; purpose: string; skill: string; icon: string }> = {
  v_respondents:       { desc: "One row per respondent with all demographics & geography resolved.", purpose: "Filter by city, zone, gender, date. Base for all %.",    skill: "demographic", icon: "👤" },
  v_brand_awareness:   { desc: "One row per respondent × brand × awareness stage.",                  purpose: "Brand funnel: TOM%, spontaneous%, total awareness%.",    skill: "awareness",   icon: "📢" },
  v_brand_nps:         { desc: "One row per respondent × brand NPS rating 0-10.",                    purpose: "NPS scores, promoter/detractor breakdowns, loyalty.",    skill: "nps",         icon: "⭐" },
  v_kitchen_ownership: { desc: "One row per respondent × kitchen appliance owned.",                  purpose: "Appliance penetration rates, ownership by demographic.", skill: "ownership",   icon: "🍳" },
  v_recent_purchase:   { desc: "One row per respondent × recently purchased appliance (ranked).",    purpose: "Which appliances bought most recently. Rank 1 = latest.", skill: "purchase",  icon: "🛒" },
  v_room_appliances:   { desc: "One row per respondent × room appliance owned.",                     purpose: "Fan/AC/bulb/geyser ownership by city or zone.",          skill: "room",        icon: "🏠" },
};

const SKILL_BADGE: Record<string, string> = {
  awareness:   "bg-sky-100 text-sky-700",
  nps:         "bg-purple-100 text-purple-700",
  ownership:   "bg-green-100 text-green-700",
  room:        "bg-yellow-100 text-yellow-700",
  purchase:    "bg-orange-100 text-orange-700",
  demographic: "bg-slate-100 text-slate-600",
};

// ── ER Diagram HTML (Cytoscape.js, mirrors Streamlit version) ─────────────────

function buildErHtml(counts: Record<string, number>): string {
  const nodes = [
    { id: "fact_respondents",       label: "Respondents",        color: "#2563EB", x: 420, y: 185 },
    { id: "dim_date",               label: "Date",               color: "#16A34A", x: 215, y:  55 },
    { id: "dim_city",               label: "City",               color: "#16A34A", x: 645, y:  70 },
    { id: "dim_zone",               label: "Zone",               color: "#16A34A", x: 130, y: 115 },
    { id: "fact_brand_awareness",   label: "Brand Awareness",    color: "#7C3AED", x: 110, y: 285 },
    { id: "fact_brand_nps",         label: "Brand NPS",          color: "#7C3AED", x: 215, y: 400 },
    { id: "fact_kitchen_ownership", label: "Kitchen Ownership",  color: "#7C3AED", x: 420, y: 445 },
    { id: "fact_recent_purchase",   label: "Recent Purchase",    color: "#7C3AED", x: 625, y: 400 },
    { id: "fact_room_appliances",   label: "Room Appliances",    color: "#7C3AED", x: 720, y: 285 },
    { id: "dim_brand",              label: "Brand",              color: "#16A34A", x:  50, y: 415 },
    { id: "dim_kitchen_appliance",  label: "Kitchen Appliance",  color: "#16A34A", x: 445, y: 540 },
    { id: "dim_room_appliance",     label: "Room Appliance",     color: "#16A34A", x: 810, y: 410 },
  ];

  const edges = [
    ["fact_respondents", "dim_date"],
    ["fact_respondents", "dim_city"],
    ["fact_respondents", "dim_zone"],
    ["fact_brand_awareness", "fact_respondents"],
    ["fact_brand_awareness", "dim_brand"],
    ["fact_brand_nps", "fact_respondents"],
    ["fact_brand_nps", "dim_brand"],
    ["fact_kitchen_ownership", "fact_respondents"],
    ["fact_kitchen_ownership", "dim_kitchen_appliance"],
    ["fact_recent_purchase", "fact_respondents"],
    ["fact_recent_purchase", "dim_kitchen_appliance"],
    ["fact_room_appliances", "fact_respondents"],
    ["fact_room_appliances", "dim_room_appliance"],
  ];

  const nodesJs = JSON.stringify(nodes.map(n => ({
    ...n,
    rows: counts[n.id] != null && counts[n.id] >= 0 ? counts[n.id].toLocaleString() : "—",
    desc: (TABLE_META[n.id]?.desc ?? ""),
  })));
  const edgesJs = JSON.stringify(edges.map(([s, t], i) => ({ id: `e${i}`, source: s, target: t })));

  return `<!DOCTYPE html><html><head>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:transparent;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
#cy{width:100%;height:100%;background:#0F172A;cursor:grab}
#cy:active{cursor:grabbing}
#tooltip{position:fixed;background:#1E293B;color:#E2E8F0;padding:10px 14px;border-radius:8px;font-size:12px;line-height:1.7;pointer-events:none;display:none;border:1px solid #334155;max-width:270px;box-shadow:0 8px 24px rgba(0,0,0,.6);z-index:9999}
#legend{position:absolute;bottom:12px;left:12px;display:flex;gap:14px;font-size:11px;color:#94A3B8;background:rgba(15,23,42,.88);padding:5px 10px;border-radius:6px;border:1px solid #1E293B;pointer-events:none}
#legend span{display:flex;align-items:center;gap:5px}
.dot{width:9px;height:9px;border-radius:2px;display:inline-block;flex-shrink:0}
#hint{position:absolute;top:8px;right:8px;font-size:10px;color:#64748B;background:rgba(15,23,42,.88);padding:3px 8px;border-radius:4px;pointer-events:none}
html,body{height:100%}
</style></head><body>
<div style="position:relative;width:100%;height:100%">
<div id="cy"></div>
<div id="legend">
  <span><span class="dot" style="background:#2563EB"></span>Core fact</span>
  <span><span class="dot" style="background:#7C3AED"></span>Fact table</span>
  <span><span class="dot" style="background:#16A34A"></span>Dimension</span>
</div>
<div id="hint">Drag nodes · Scroll to zoom · Drag canvas to pan</div>
</div>
<div id="tooltip"></div>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.26.0/dist/cytoscape.min.js"></script>
<script>
const nodesData=${nodesJs};
const edgesData=${edgesJs};
const elements=[];
nodesData.forEach(n=>{elements.push({data:{id:n.id,label:n.label+'\\n'+n.id,humanLabel:n.label,techName:n.id,color:n.color,desc:n.desc,rows:n.rows},position:{x:n.x,y:n.y}})});
edgesData.forEach(e=>{elements.push({data:{id:e.id,source:e.source,target:e.target}})});
const cy=cytoscape({container:document.getElementById('cy'),elements,layout:{name:'preset'},
style:[
{selector:'node',style:{'background-color':'data(color)','label':'data(label)','color':'#FFFFFF','text-valign':'center','text-halign':'center','font-size':'9.5px','font-family':'system-ui,sans-serif','width':'92px','height':'46px','shape':'round-rectangle','text-wrap':'wrap','text-max-width':'86px','border-width':1.5,'border-color':'rgba(255,255,255,.15)','transition-property':'border-color,border-width','transition-duration':'80ms'}},
{selector:'#fact_respondents',style:{'width':'104px','height':'50px','font-size':'10px','border-width':2.5,'border-color':'rgba(255,255,255,.3)','font-weight':'bold'}},
{selector:'node.hover',style:{'border-color':'#F59E0B','border-width':3}},
{selector:'edge',style:{'width':1.5,'line-color':'#334155','curve-style':'bezier','target-arrow-shape':'vee','target-arrow-color':'#475569','arrow-scale':0.85,'opacity':0.6,'transition-property':'opacity,line-color,width','transition-duration':'80ms'}},
{selector:'edge.highlighted',style:{'line-color':'#60A5FA','target-arrow-color':'#60A5FA','opacity':1,'width':2.5}},
],userZoomingEnabled:true,userPanningEnabled:true,minZoom:0.25,maxZoom:4,wheelSensitivity:0.25});
const tooltip=document.getElementById('tooltip');
cy.on('mouseover','node',function(e){const d=e.target.data();tooltip.innerHTML='<div style="font-weight:700;font-size:13px;margin-bottom:2px">'+d.humanLabel+'</div><div style="color:#94A3B8;font-size:10px;font-family:monospace;margin-bottom:6px">'+d.techName+'</div><div style="margin-bottom:6px;color:#CBD5E1">'+d.desc+'</div><div style="color:#60A5FA;font-weight:600">'+d.rows+' rows</div>';tooltip.style.display='block';e.target.addClass('hover');e.target.connectedEdges().addClass('highlighted')});
cy.on('mouseout','node',function(e){tooltip.style.display='none';e.target.removeClass('hover');e.target.connectedEdges().removeClass('highlighted')});
document.getElementById('cy').addEventListener('mousemove',function(e){if(tooltip.style.display!=='none'){tooltip.style.left=(e.clientX+18)+'px';tooltip.style.top=(e.clientY-10)+'px'}});
document.getElementById('cy').addEventListener('mouseleave',function(){tooltip.style.display='none'});
</script></body></html>`;
}

// ── Stats bar ──────────────────────────────────────────────────────────────────

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 bg-surface-lowest border border-outline-variant/30 rounded-xl px-4 py-3 shadow-sm">
      <span className="text-xs text-secondary font-medium">{label}</span>
      <span className="text-xl font-headline font-semibold text-primary">{value}</span>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function SchemaView() {
  const [data, setData]       = useState<SchemaData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [tab, setTab]         = useState<"er" | "views" | "tables">("er");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const fetchSchema = async () => {
    setLoading(true); setError(null);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || ""}/api/schema`, {
        headers: {
          "Bypass-Tunnel-Reminder": "true",
          "ngrok-skip-browser-warning": "69420"
        }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchSchema(); }, []);

  const toggle = (name: string) =>
    setExpanded(prev => { const n = new Set(prev); n.has(name) ? n.delete(name) : n.add(name); return n; });

  const fmt = (n: number) => n >= 0 ? n.toLocaleString() : "—";

  const counts = data?.counts ?? {};
  const erHtml = data ? buildErHtml(counts) : "";

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 px-6 pt-8 pb-4 border-b border-outline-variant/20">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <Database className="w-6 h-6 text-primary" />
              <h1 className="font-headline text-2xl font-semibold text-on-surface">Schema Explorer</h1>
            </div>
            <p className="text-secondary text-sm ml-9">OX Wave 1 · SQLite star schema · live row counts</p>
          </div>
          <button onClick={fetchSchema} disabled={loading}
            className="flex items-center gap-1.5 text-sm text-secondary hover:text-primary transition-colors px-3 py-1.5 rounded-xl hover:bg-surface-variant/50 disabled:opacity-50">
            <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
            Refresh
          </button>
        </div>

        {/* Stats */}
        {data && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            <StatCard label="Respondents"  value={fmt(counts.fact_respondents)} />
            <StatCard label="Brand events" value={fmt(counts.fact_brand_awareness)} />
            <StatCard label="NPS ratings"  value={fmt(counts.fact_brand_nps)} />
            <StatCard label="Views"        value="6" />
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-1">
          {([["er", "ER Diagram"], ["views", "Views (query these)"], ["tables", "Raw Tables"]] as const).map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)}
              className={cn(
                "px-4 py-2 rounded-xl text-sm font-medium transition-all",
                tab === id ? "bg-primary text-on-primary" : "text-secondary hover:bg-surface-variant/50",
              )}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <AnimatePresence mode="wait">
          {loading ? (
            <motion.div 
              key="loading"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="flex items-center justify-center py-20 gap-3 text-secondary"
            >
              <RefreshCw className="w-5 h-5 animate-spin" />
              <span className="text-sm">Loading schema…</span>
            </motion.div>
          ) : error ? (
            <motion.div 
              key="error"
              initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
              className="flex items-center gap-3 bg-red-50 border border-red-200 rounded-2xl px-4 py-3 text-red-700 text-sm"
            >
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {error} — make sure the Python API is running.
            </motion.div>
          ) : (
            <motion.div
              key={tab}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2 }}
            >
              {/* ER Diagram tab */}
              {tab === "er" && data && (
                <div className="flex flex-col gap-4">
                  <p className="text-xs text-secondary/70">
                    Drag nodes to rearrange · Scroll to zoom · Hover for details
                  </p>
                  <div className="w-full rounded-2xl overflow-hidden border border-outline-variant/20 shadow-sm" style={{ height: 560 }}>
                    <iframe
                      ref={iframeRef}
                      srcDoc={erHtml}
                      className="w-full h-full border-0"
                      title="ER Diagram"
                      sandbox="allow-scripts"
                    />
                  </div>
                  <div className="text-xs text-secondary/60 leading-relaxed bg-surface-lowest border border-outline-variant/20 rounded-xl p-4">
                    <strong className="text-on-surface">Reading the diagram:</strong>{" "}
                    <span className="text-blue-600 font-medium">Blue</span> = <code>fact_respondents</code> hub.{" "}
                    <span className="text-violet-600 font-medium">Purple</span> = fact tables (one row per event).{" "}
                    <span className="text-green-600 font-medium">Green</span> = dimension lookup tables.
                    All views pre-join these so the LLM writes simpler SQL.
                  </div>
                </div>
              )}

              {/* Views tab */}
              {tab === "views" && data && (
                <div className="flex flex-col gap-3">
                  <p className="text-xs text-secondary/70 mb-2">
                    Always query these in chat — pre-joined with all dimension labels.
                  </p>
                  {Object.entries(VIEW_META).map(([name, meta], i) => {
                    const cnt = counts[name] ?? -1;
                    const cols = data.columns[name] ?? [];
                    const sample = data.samples[name] ?? [];
                    const isOpen = expanded.has(name);
                    return (
                      <motion.div key={name}
                        layout
                        initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
                        className="border border-outline-variant/30 rounded-2xl overflow-hidden bg-surface-lowest shadow-sm"
                      >
                        <button onClick={() => toggle(name)}
                          className="w-full px-4 py-3.5 flex items-start gap-3 hover:bg-surface-variant/20 transition-colors text-left">
                          <span className="text-2xl">{meta.icon}</span>
                          <div className="flex-1 min-w-0">
                            <div className="flex flex-wrap items-center gap-2 mb-1">
                              <span className="font-mono text-sm font-semibold text-on-surface">{name}</span>
                              <span className={cn("text-[10px] font-medium px-1.5 py-0.5 rounded-full", SKILL_BADGE[meta.skill])}>
                                {meta.skill}
                              </span>
                              <span className="text-[10px] text-secondary/60">
                                {cnt >= 0 ? `${cnt.toLocaleString()} rows` : "—"}
                              </span>
                            </div>
                            <p className="text-xs text-secondary leading-snug">{meta.desc}</p>
                            <p className="text-xs text-primary/70 mt-0.5">Use for: {meta.purpose}</p>
                          </div>
                          {isOpen ? <ChevronDown className="w-4 h-4 text-secondary flex-shrink-0 mt-1" /> : <ChevronRight className="w-4 h-4 text-secondary flex-shrink-0 mt-1" />}
                        </button>

                        <AnimatePresence>
                          {isOpen && (
                            <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }}
                              className="overflow-hidden">
                              <div className="border-t border-outline-variant/20 p-4 bg-surface-variant/10 grid grid-cols-1 md:grid-cols-2 gap-4">
                                {/* Columns */}
                                <div>
                                  <p className="text-[10px] uppercase tracking-wider text-secondary/60 font-semibold mb-2">Columns</p>
                                  <div className="overflow-x-auto">
                                    <table className="w-full text-xs">
                                      <thead><tr className="text-secondary/70">
                                        <th className="text-left pb-1 pr-3 font-medium">Name</th>
                                        <th className="text-left pb-1 pr-3 font-medium">Type</th>
                                        <th className="text-left pb-1 font-medium">PK</th>
                                      </tr></thead>
                                      <tbody>{cols.map(c => (
                                        <tr key={c.name} className="border-t border-outline-variant/10">
                                          <td className="py-1 pr-3 font-mono text-on-surface/80">{c.name}</td>
                                          <td className="py-1 pr-3 font-mono text-primary/70">{c.type}</td>
                                          <td className="py-1">{c.pk ? "✓" : ""}</td>
                                        </tr>
                                      ))}</tbody>
                                    </table>
                                  </div>
                                </div>
                                {/* Sample rows */}
                                {sample.length > 0 && (
                                  <div>
                                    <p className="text-[10px] uppercase tracking-wider text-secondary/60 font-semibold mb-2">Sample rows</p>
                                    <div className="overflow-x-auto">
                                      <table className="w-full text-[11px]">
                                        <thead><tr>{Object.keys(sample[0]).slice(0, 5).map(k => (
                                          <th key={k} className="text-left pb-1 pr-2 font-medium text-secondary/70 whitespace-nowrap">{k}</th>
                                        ))}</tr></thead>
                                        <tbody>{sample.map((row, ri) => (
                                          <tr key={ri} className="border-t border-outline-variant/10">
                                            {Object.values(row).slice(0, 5).map((v, vi) => (
                                              <td key={vi} className="py-1 pr-2 text-on-surface/70 font-mono whitespace-nowrap">{String(v ?? "")}</td>
                                            ))}
                                          </tr>
                                        ))}</tbody>
                                      </table>
                                    </div>
                                  </div>
                                )}
                              </div>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </motion.div>
                    );
                  })}
                </div>
              )}

              {/* Tables tab */}
              {tab === "tables" && data && (
                <div className="flex flex-col gap-4">
                  <p className="text-xs text-secondary/70 mb-2">Raw tables — for reference. Chat uses views, not these.</p>
                  {(["fact", "dim"] as const).map(kind => {
                    const kindLabel = kind === "fact" ? "Fact Tables" : "Dimension Tables";
                    const kindColor = kind === "fact" ? "text-violet-700" : "text-green-700";
                    const entries = Object.entries(TABLE_META).filter(([, m]) => m.kind === kind);
                    return (
                      <div key={kind}>
                        <h3 className={cn("text-sm font-semibold mb-3", kindColor)}>{kindLabel}</h3>
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                          {entries.map(([name, meta], i) => {
                            const cnt = counts[name] ?? -1;
                            const cols = data.columns[name] ?? [];
                            const isOpen = expanded.has(name);
                            return (
                              <motion.div key={name}
                                layout
                                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}
                                className="border border-outline-variant/30 rounded-2xl overflow-hidden bg-surface-lowest shadow-sm"
                              >
                                <button onClick={() => toggle(name)}
                                  className="w-full px-3 py-3 flex items-start gap-2 hover:bg-surface-variant/20 transition-colors text-left">
                                  <span>{meta.icon}</span>
                                  <div className="flex-1 min-w-0">
                                    <p className="font-mono text-xs font-semibold text-on-surface truncate">{name}</p>
                                    <p className="text-[10px] text-secondary/70 mt-0.5">{cnt >= 0 ? `${cnt.toLocaleString()} rows` : "—"}</p>
                                    <p className="text-[10px] text-secondary leading-snug mt-1">{meta.desc}</p>
                                  </div>
                                  {isOpen ? <ChevronDown className="w-3.5 h-3.5 text-secondary flex-shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-secondary flex-shrink-0" />}
                                </button>
                                <AnimatePresence>
                                  {isOpen && cols.length > 0 && (
                                    <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }} className="overflow-hidden">
                                      <div className="border-t border-outline-variant/20 px-3 py-2 bg-surface-variant/10">
                                        <table className="w-full text-[11px]">
                                          <thead><tr className="text-secondary/60">
                                            <th className="text-left pb-1 pr-2 font-medium">Column</th>
                                            <th className="text-left pb-1 font-medium">Type</th>
                                          </tr></thead>
                                          <tbody>{cols.map(c => (
                                            <tr key={c.name} className="border-t border-outline-variant/10">
                                              <td className="py-0.5 pr-2 font-mono text-on-surface/80">{c.name}</td>
                                              <td className="py-0.5 font-mono text-primary/60">{c.type}</td>
                                            </tr>
                                          ))}</tbody>
                                        </table>
                                      </div>
                                    </motion.div>
                                  )}
                                </AnimatePresence>
                              </motion.div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
