import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import { History, RefreshCw, Clock, Database, AlertCircle, ChevronDown, ChevronRight, CheckCircle, XCircle } from "lucide-react";
import { cn } from "../../lib/utils";

interface HistoryRow {
  timestamp: string;
  session_id: string;
  complexity: string;
  question: string;
  skill: string;
  sql: string;
  row_count: number;
  chart_type: string;
  latency_ms: number;
  validation_ok: boolean;
  error: string;
}

const COMPLEXITY_COLOR: Record<string, string> = {
  simple:      "bg-green-100 text-green-700",
  contextual:  "bg-blue-100 text-blue-700",
  complex:     "bg-violet-100 text-violet-700",
  summary:     "bg-slate-100 text-slate-600",
};

const SKILL_COLOR: Record<string, string> = {
  awareness:   "bg-sky-100 text-sky-700",
  nps:         "bg-purple-100 text-purple-700",
  ownership:   "bg-green-100 text-green-700",
  room:        "bg-yellow-100 text-yellow-700",
  purchase:    "bg-orange-100 text-orange-700",
  demographic: "bg-gray-100 text-gray-700",
  general:     "bg-slate-100 text-slate-600",
  summary:     "bg-indigo-100 text-indigo-700",
};

export function QueryHistoryView() {
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<string>("all");

  const fetchHistory = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || ""}/api/history?limit=50`, {
        headers: {
          "Bypass-Tunnel-Reminder": "true",
          "ngrok-skip-browser-warning": "69420"
        }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRows(data.rows || []);
    } catch (e: any) {
      setError(e.message || "Failed to load history");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchHistory(); }, []);

  const toggle = (i: number) =>
    setExpanded(prev => {
      const n = new Set(prev);
      n.has(i) ? n.delete(i) : n.add(i);
      return n;
    });

  const skills = ["all", ...Array.from(new Set(rows.map(r => r.skill).filter(Boolean)))];

  const filtered = filter === "all" ? rows : rows.filter(r => r.skill === filter);

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto px-6 py-8 max-w-4xl mx-auto w-full">
      {/* Header */}
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 mb-1">
            <History className="w-6 h-6 text-primary" />
            <h1 className="font-headline text-2xl font-semibold text-on-surface">Query History</h1>
          </div>
          <button
            onClick={fetchHistory}
            disabled={loading}
            className="flex items-center gap-1.5 text-sm text-secondary hover:text-primary transition-colors px-3 py-1.5 rounded-xl hover:bg-surface-variant/50 disabled:opacity-50"
          >
            <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
            Refresh
          </button>
        </div>
        <p className="text-secondary text-sm ml-9">Recent queries logged to Google Sheets</p>
      </motion.div>

      {/* Skill filter pills */}
      {rows.length > 0 && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.05 }}
          className="flex flex-wrap gap-2 mb-5">
          {skills.map(s => (
            <button key={s}
              onClick={() => setFilter(s)}
              className={cn(
                "px-3 py-1 rounded-full text-xs font-medium transition-all capitalize",
                filter === s
                  ? "bg-primary text-on-primary"
                  : "bg-surface-variant/50 text-secondary hover:bg-surface-variant",
              )}>
              {s}
            </button>
          ))}
        </motion.div>
      )}

      {/* States */}
      {loading && (
        <div className="flex items-center justify-center py-20 gap-3 text-secondary">
          <RefreshCw className="w-5 h-5 animate-spin" />
          <span className="text-sm">Loading history…</span>
        </div>
      )}

      {!loading && error && (
        <div className="flex items-center gap-3 bg-red-50 border border-red-200 rounded-2xl px-4 py-3 text-red-700 text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {!loading && !error && filtered.length === 0 && (
        <div className="text-center py-20 text-secondary">
          <History className="w-10 h-10 mx-auto mb-3 opacity-20" />
          <p className="text-sm">No queries logged yet.</p>
          <p className="text-xs mt-1 opacity-60">Ask a question in the Chat view to get started.</p>
        </div>
      )}

      {/* Rows */}
      {!loading && !error && (
        <div className="flex flex-col gap-2">
          <AnimatePresence>
            {filtered.map((row, i) => (
              <motion.div key={i}
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.03 }}
                className="border border-outline-variant/30 rounded-2xl overflow-hidden bg-surface-lowest shadow-sm"
              >
                {/* Row header */}
                <button onClick={() => toggle(i)}
                  className="w-full px-4 py-3.5 flex items-start gap-3 hover:bg-surface-variant/20 transition-colors text-left">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-on-surface leading-snug mb-1.5 truncate">
                      {row.question}
                    </p>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {row.skill && (
                        <span className={cn("text-[10px] font-medium px-1.5 py-0.5 rounded-full", SKILL_COLOR[row.skill] || "bg-slate-100 text-slate-600")}>
                          {row.skill}
                        </span>
                      )}
                      {row.complexity && (
                        <span className={cn("text-[10px] font-medium px-1.5 py-0.5 rounded-full", COMPLEXITY_COLOR[row.complexity] || "bg-slate-100 text-slate-600")}>
                          {row.complexity}
                        </span>
                      )}
                      {row.chart_type && row.chart_type !== "none" && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-surface-variant/60 text-secondary">
                          📊 {row.chart_type}
                        </span>
                      )}
                      {row.row_count > 0 && (
                        <span className="flex items-center gap-0.5 text-[10px] text-secondary/70">
                          <Database className="w-2.5 h-2.5" /> {row.row_count} rows
                        </span>
                      )}
                      {row.latency_ms > 0 && (
                        <span className="flex items-center gap-0.5 text-[10px] text-secondary/70">
                          <Clock className="w-2.5 h-2.5" /> {(row.latency_ms / 1000).toFixed(1)}s
                        </span>
                      )}
                      {row.validation_ok !== undefined && (
                        row.validation_ok
                          ? <CheckCircle className="w-3 h-3 text-green-500" />
                          : <XCircle className="w-3 h-3 text-amber-500" />
                      )}
                    </div>
                  </div>
                  <div className="flex-shrink-0 flex items-center gap-2">
                    <span className="text-[10px] text-secondary/50 whitespace-nowrap">
                      {row.timestamp ? new Date(row.timestamp).toLocaleString("en-IN", {
                        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                      }) : ""}
                    </span>
                    {expanded.has(i)
                      ? <ChevronDown className="w-4 h-4 text-secondary" />
                      : <ChevronRight className="w-4 h-4 text-secondary" />}
                  </div>
                </button>

                {/* Expanded SQL */}
                <AnimatePresence>
                  {expanded.has(i) && row.sql && (
                    <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }}
                      className="overflow-hidden">
                      <div className="border-t border-outline-variant/20 px-4 py-3 bg-inverse-surface/5">
                        <p className="text-[10px] uppercase tracking-wider text-secondary/60 mb-2 font-semibold">SQL</p>
                        <pre className="font-mono text-[11px] text-on-surface/80 whitespace-pre-wrap overflow-x-auto leading-relaxed">
                          {row.sql}
                        </pre>
                        {row.error && (
                          <div className="mt-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-1.5">
                            ⚠️ {row.error}
                          </div>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
