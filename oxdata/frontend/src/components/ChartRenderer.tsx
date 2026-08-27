import React, { useMemo } from 'react';
import {
  BarChart, Bar,
  LineChart, Line,
  AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, Cell,
} from 'recharts';

interface ChartConfig {
  [key: string]: { color: string; label: string };
}

interface ChartDataPayload {
  type: 'BarChart' | 'GroupedBarChart' | 'LineChart' | 'AreaChart'
      | 'NpsWaterfall' | 'FunnelChart';
  data: any[];
  config: ChartConfig;
}

const TOOLTIP_STYLE = {
  contentStyle: { backgroundColor: '#f5f4ed', borderRadius: '12px', border: '1px solid #e3e3dc', color: '#1b1c18' },
  itemStyle: { color: '#1b1c18', fontSize: '13px', fontWeight: 500 },
};
const AXIS_TICK = { fill: '#89726b', fontSize: 12 };
const GRID_PROPS = { strokeDasharray: '3 3', vertical: false, stroke: '#e3e3dc' };

export function ChartRenderer({ content }: { content: string }) {
  const parsed = useMemo(() => {
    try { return JSON.parse(content) as ChartDataPayload; }
    catch { return null; }
  }, [content]);

  if (!parsed || !parsed.data || !parsed.config) {
    return <div className="text-red-500 bg-red-500/10 p-4 rounded-xl text-sm border border-red-500/20">Failed to render chart: Invalid data format.</div>;
  }

  return (
    <div className="w-full h-[360px] my-6 p-6 bg-surface-lowest border border-outline-variant/30 rounded-2xl shadow-sm relative overflow-hidden">
      <ResponsiveContainer width="100%" height="100%">
        {renderChart(parsed)}
      </ResponsiveContainer>
    </div>
  );
}

function renderChart(parsed: ChartDataPayload) {
  const { type, data, config } = parsed;
  const keys = Object.keys(config);

  switch (type) {

    case 'BarChart':
      return (
        <BarChart data={data} margin={{ top: 20, right: 20, left: -20, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} dy={10}
                 angle={data.length > 8 ? -35 : 0} textAnchor={data.length > 8 ? 'end' : 'middle'}
                 height={data.length > 8 ? 60 : 30} interval={0} />
          <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <Tooltip {...TOOLTIP_STYLE} />
          <Legend wrapperStyle={{ paddingTop: '20px' }} />
          {keys.map(k => (
            <Bar key={k} dataKey={k} name={config[k].label} fill={config[k].color} radius={[4,4,0,0]} />
          ))}
        </BarChart>
      );

    case 'GroupedBarChart':
      return (
        <BarChart data={data} margin={{ top: 20, right: 20, left: -20, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} dy={10}
                 angle={data.length > 6 ? -35 : 0} textAnchor={data.length > 6 ? 'end' : 'middle'}
                 height={data.length > 6 ? 60 : 30} interval={0} />
          <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <Tooltip {...TOOLTIP_STYLE} />
          <Legend wrapperStyle={{ paddingTop: '20px' }} />
          {keys.map(k => (
            <Bar key={k} dataKey={k} name={config[k].label} fill={config[k].color}
                 radius={[3,3,0,0]} />
          ))}
        </BarChart>
      );

    case 'NpsWaterfall': {
      // Horizontal stacked bar: detractors | passives | promoters
      // Use layout="vertical" for horizontal bars
      const orderedKeys = ['detractors', 'passives', 'promoters'].filter(k => k in config);
      return (
        <BarChart data={data} layout="vertical" margin={{ top: 10, right: 80, left: 80, bottom: 10 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e3e3dc" />
          <XAxis type="number" tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} width={75} />
          <Tooltip {...TOOLTIP_STYLE} />
          <Legend wrapperStyle={{ paddingTop: '20px' }} />
          {orderedKeys.map(k => (
            <Bar key={k} dataKey={k} name={config[k].label} fill={config[k].color} stackId="nps" />
          ))}
        </BarChart>
      );
    }

    case 'FunnelChart': {
      // Render as horizontal bars sorted by value desc (funnel feel)
      const sorted = [...data].sort((a, b) => b.value - a.value);
      const funnelColors = ['#4A90D9', '#7B61FF', '#00BCD4'];
      return (
        <BarChart data={sorted} layout="vertical" margin={{ top: 10, right: 80, left: 60, bottom: 10 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e3e3dc" />
          <XAxis type="number" tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} width={55} />
          <Tooltip {...TOOLTIP_STYLE} />
          <Bar dataKey="value" name={Object.values(config)[0]?.label || 'Value'} radius={[0,4,4,0]}>
            {sorted.map((_: any, i: number) => (
              <Cell key={i} fill={funnelColors[i % funnelColors.length]} />
            ))}
          </Bar>
        </BarChart>
      );
    }

    case 'LineChart':
      return (
        <LineChart data={data} margin={{ top: 20, right: 20, left: -20, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} dy={10} />
          <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <Tooltip {...TOOLTIP_STYLE} />
          <Legend wrapperStyle={{ paddingTop: '20px' }} />
          {keys.map(k => (
            <Line key={k} type="monotone" dataKey={k} name={config[k].label}
                  stroke={config[k].color} strokeWidth={3}
                  dot={{ r: 4, strokeWidth: 2 }} activeDot={{ r: 6 }} />
          ))}
        </LineChart>
      );

    case 'AreaChart':
      return (
        <AreaChart data={data} margin={{ top: 20, right: 20, left: -20, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} dy={10} />
          <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <Tooltip {...TOOLTIP_STYLE} />
          <Legend wrapperStyle={{ paddingTop: '20px' }} />
          {keys.map(k => (
            <Area key={k} type="monotone" dataKey={k} name={config[k].label}
                  stroke={config[k].color} fill={config[k].color} fillOpacity={0.2} strokeWidth={2} />
          ))}
        </AreaChart>
      );

    default:
      return (
        <BarChart data={data} margin={{ top: 20, right: 20, left: -20, bottom: 0 }}>
          <XAxis dataKey="name" tick={AXIS_TICK} />
          <YAxis tick={AXIS_TICK} />
          <Tooltip {...TOOLTIP_STYLE} />
          {keys.map(k => <Bar key={k} dataKey={k} fill={config[k].color} />)}
        </BarChart>
      );
  }
}

// Standalone chart renderer from a chart spec object (not embedded in markdown)
export function ChartFromSpec({ spec }: { spec: Record<string, unknown> | null }) {
  if (!spec) return null;
  const parsed = spec as ChartDataPayload;
  if (!parsed.data || !parsed.config) return null;

  return (
    <div className="w-full h-[360px] my-4 p-6 bg-surface-lowest border border-outline-variant/30 rounded-2xl shadow-sm relative overflow-hidden">
      <ResponsiveContainer width="100%" height="100%">
        {renderChart(parsed)}
      </ResponsiveContainer>
    </div>
  );
}
