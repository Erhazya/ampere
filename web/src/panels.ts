import type { EChartsCoreOption } from 'echarts/core';
import type { Day, Point, Recent, SeriesId } from './api/recent';

/** The charts of the Data screen, as text.ts names their words. */
export type PanelId = 'price' | 'consumption' | 'co2' | 'solar' | 'temperature';

/** One chart of the Data screen: a quantity, measured or published, and its forecast if any. */
export interface Panel {
  id: PanelId;
  measured: SeriesId;
  forecast?: SeriesId;
  /** From the unit of the export to the unit on screen: MW to GW for the consumption. */
  scale: number;
  digits: number;
  /** Whether the scale starts at zero, or at the lowest value. */
  zero: boolean;
}

/** The five charts, in the order of the screen (ADR 029). Their words are in text.ts. */
export const PANELS: readonly Panel[] = [
  { id: 'price', measured: 'price', scale: 1, digits: 2, zero: true },
  {
    id: 'consumption',
    measured: 'consumption',
    forecast: 'rte_forecast',
    scale: 1 / 1000,
    digits: 1,
    zero: false,
  },
  { id: 'co2', measured: 'co2', scale: 1, digits: 0, zero: true },
  { id: 'solar', measured: 'solar', scale: 1, digits: 0, zero: true },
  {
    id: 'temperature',
    measured: 'temperature',
    forecast: 'temperature_forecast',
    scale: 1,
    digits: 1,
    zero: false,
  },
];

/** The room above and below the curves, in pixels, so that a line of 2 px at the top or at the
 * bottom of its scale stays whole. The screen places its marks with the same margin. */
export const MARGIN = 6;

/** The colors of the charts, read from the style sheet: the series steps were validated for
 * the dark ground (dataviz skill, ADR 029). */
export interface Colors {
  line: string;
  ink: string;
  muted: string;
  measured: string;
  forecast: string;
}

/** The latest point of a series, if it has one. */
export function latest(points: readonly Point[] | undefined): Point | undefined {
  return points?.at(-1);
}

/** The point of a series at an instant, or the nearest one within `slack` milliseconds. */
export function pointAt(
  points: readonly Point[] | undefined,
  instant: number,
  slack: number,
): Point | undefined {
  if (!points || points.length === 0) return undefined;
  let low = 0;
  let high = points.length - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (points[middle][0] < instant) low = middle + 1;
    else high = middle;
  }
  const candidates = [points[low - 1], points[low]].filter((point) => point !== undefined);
  const nearest = candidates.reduce((best, point) =>
    Math.abs(point[0] - instant) < Math.abs(best[0] - instant) ? point : best,
  );
  return Math.abs(nearest[0] - instant) <= slack ? nearest : undefined;
}

/** The series of a chart: what was measured or published, then its forecast if any. */
export function seriesOf(panel: Panel): SeriesId[] {
  return panel.forecast ? [panel.measured, panel.forecast] : [panel.measured];
}

/**
 * The scale of a chart, in the unit on screen: from its lowest value to its highest, forecast
 * included, and from zero for a quantity that starts there, rounded outwards to whole units as
 * the screen writes them. Null when the chart has no value.
 */
export function extent(panel: Panel, recent: Recent): [number, number] | null {
  const values = seriesOf(panel).flatMap((id) =>
    (recent.series.get(id) ?? []).map(([, value]) => value * panel.scale),
  );
  if (values.length === 0) return null;
  let low = Math.min(...values);
  let high = Math.max(...values);
  if (panel.zero) {
    low = Math.min(low, 0);
    high = Math.max(high, 0);
  }
  low = Math.floor(low);
  high = Math.ceil(high);
  return [low, Math.max(high, low + 1)];
}

/** The runs of consecutive days that share a name, as [start, end, name]. */
export function bands(
  days: readonly Day[],
  name: (day: Day) => string | null,
): [number, number, string][] {
  const runs: [number, number, string][] = [];
  for (const day of days) {
    const label = name(day);
    const last = runs.at(-1);
    if (label === null) continue;
    if (last && last[2] === label && last[1] === day.start) last[1] = day.end;
    else runs.push([day.start, day.end, label]);
  }
  return runs;
}

/**
 * The option of one chart: its series in the unit on screen, on the scale of `extent`, with the
 * midnights of Paris, the line of zero, the holidays, tomorrow and now. The chart only draws:
 * its scale, the cursor and the values under it are HTML (DataCharts), so that no text from a
 * source ever goes through ECharts.
 */
export function panelOption(
  panel: Panel,
  recent: Recent,
  now: number,
  colors: Colors,
): EChartsCoreOption {
  const scaled = (id: SeriesId | undefined) =>
    (id ? (recent.series.get(id) ?? []) : []).map(([instant, value]) => [
      instant,
      value * panel.scale,
    ]);
  const lastDay = recent.days.at(-1);
  const [low, high] = extent(panel, recent) ?? [0, 1];
  const guides = {
    silent: true,
    symbol: 'none',
    label: { show: false },
    data: [
      ...recent.days.slice(1).map((day) => ({
        xAxis: day.start,
        lineStyle: { color: colors.line, type: 'solid', width: 1 },
      })),
      ...(now > recent.start && now < recent.end
        ? [{ xAxis: now, lineStyle: { color: colors.muted, type: [3, 3], width: 1 } }]
        : []),
      ...(low <= 0 && 0 <= high
        ? [{ yAxis: 0, lineStyle: { color: colors.line, type: 'solid', width: 1 } }]
        : []),
    ],
  };
  const areas = {
    silent: true,
    label: { show: false },
    data: [
      ...bands(recent.days, (day) => day.schoolHolidays).map(([start, end]) => [
        { xAxis: start, itemStyle: { color: colors.ink, opacity: 0.04 } },
        { xAxis: end },
      ]),
      ...bands(recent.days, (day) => day.publicHoliday).map(([start, end]) => [
        { xAxis: start, itemStyle: { color: colors.ink, opacity: 0.08 } },
        { xAxis: end },
      ]),
      ...(lastDay
        ? [
            [
              { xAxis: lastDay.start, itemStyle: { color: colors.ink, opacity: 0.06 } },
              { xAxis: recent.end },
            ],
          ]
        : []),
    ],
  };
  const line = (id: SeriesId, color: string, dashed: boolean) => ({
    type: 'line',
    name: id,
    data: scaled(id),
    showSymbol: false,
    silent: true,
    sampling: 'lttb',
    lineStyle: { width: 2, color, type: dashed ? [6, 4] : 'solid' },
    itemStyle: { color },
    emphasis: { disabled: true },
  });
  const series = [
    ...(panel.forecast ? [line(panel.forecast, colors.forecast, true)] : []),
    { ...line(panel.measured, colors.measured, false), markLine: guides, markArea: areas },
  ];
  return {
    animation: false,
    grid: { left: 0, right: 0, top: MARGIN, bottom: MARGIN },
    xAxis: { type: 'value', min: recent.start, max: recent.end, show: false },
    yAxis: { type: 'value', min: low, max: high, show: false },
    series,
  };
}
