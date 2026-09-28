import { describe, expect, it } from 'vitest';
import { bands, extent, PANELS, panelOption, pointAt } from './panels';
import { DAY, QUARTER, recent, START } from './test/recent';

const COLORS = { line: 'L', ink: 'I', muted: 'M', measured: 'A', forecast: 'B' };

describe('pointAt', () => {
  const points = [
    [START, 1],
    [START + QUARTER, 2],
    [START + 2 * QUARTER, 3],
  ] as const;

  it('finds the point at an instant, or the nearest within the slack', () => {
    expect(pointAt(points, START + QUARTER, 0)).toEqual([START + QUARTER, 2]);
    expect(pointAt(points, START + QUARTER + 60_000, 5 * 60_000)).toEqual([START + QUARTER, 2]);
    expect(pointAt(points, START + 2 * QUARTER - 60_000, 5 * 60_000)).toEqual([
      START + 2 * QUARTER,
      3,
    ]);
    expect(pointAt(points, START + 3 * QUARTER, 5 * 60_000)).toBeUndefined();
    expect(pointAt([], START, DAY)).toBeUndefined();
    expect(pointAt(undefined, START, DAY)).toBeUndefined();
  });
});

describe('extent', () => {
  const [price, consumption, , solar, temperature] = PANELS;
  const series = (id: string, values: number[]) => ({
    id,
    source: id === 'price' ? 'smard' : id.startsWith('temperature') ? 'openmeteo' : 'eco2mix',
    unit: { price: 'EUR/MWh', temperature: 'degC', temperature_forecast: 'degC' }[id] ?? 'MW',
    points: values.map((value, n) => [START + n * QUARTER, value]),
  });
  const withSeries = (...items: ReturnType<typeof series>[]) => recent({ series: items });

  it('runs from zero to the highest value, in whole units, for a quantity that starts there', () => {
    expect(extent(price, withSeries(series('price', [-2.03, 120, 390.4])))).toEqual([-3, 391]);
    expect(extent(solar, withSeries(series('solar', [0, 2544])))).toEqual([0, 2544]);
    expect(extent(price, withSeries(series('price', [80, 175])))).toEqual([0, 175]);
  });

  it('runs from the lowest value to the highest, the forecast included, in the unit on screen', () => {
    const data = withSeries(
      series('consumption', [30_806, 45_000]),
      series('rte_forecast', [30_600, 49_623]),
    );
    expect(extent(consumption, data)).toEqual([30, 50]);
    // A bound already whole stays as it is.
    expect(extent(consumption, withSeries(series('consumption', [45_000, 46_000])))).toEqual([
      45, 46,
    ]);
  });

  it('keeps a height when the series is flat, and gives none without a value', () => {
    expect(extent(temperature, withSeries(series('temperature', [15, 15])))).toEqual([15, 16]);
    expect(extent(solar, withSeries(series('solar', [])))).toBeNull();
  });
});

describe('bands', () => {
  it('joins the consecutive days that share a name', () => {
    const days = recent().days;
    expect(bands(days, (day) => day.schoolHolidays)).toEqual([
      [START + DAY, START + 3 * DAY, 'Vacances de test'],
    ]);
    expect(bands(days, (day) => day.publicHoliday)).toEqual([
      [START + 3 * DAY, START + 4 * DAY, 'Jour de test'],
    ]);
  });
});

describe('panelOption', () => {
  const data = recent();
  const [, consumption] = PANELS;
  const option = panelOption(consumption, data, START + 7 * DAY + 12 * 3_600_000, COLORS) as {
    xAxis: { min: number; max: number };
    series: {
      name: string;
      data: number[][];
      lineStyle: { color: string; type: unknown };
      markLine?: { data: { xAxis?: number; yAxis?: number }[] };
      markArea?: { data: { xAxis: number }[][] };
    }[];
    yAxis: { min: number; max: number; show: boolean };
    tooltip?: unknown;
  };

  it('draws the forecast under the measured series, dashed, in the unit on screen', () => {
    expect(option.series.map((series) => series.name)).toEqual(['rte_forecast', 'consumption']);
    expect(option.series[0].lineStyle).toEqual({ width: 2, color: 'B', type: [6, 4] });
    expect(option.series[1].lineStyle).toEqual({ width: 2, color: 'A', type: 'solid' });
    // MW to GW.
    expect(option.series[1].data[0]).toEqual([START, 45.9]);
  });

  it('scales the chart from its lowest value to its highest, without an axis of its own', () => {
    expect(option.yAxis).toMatchObject({ min: 42, max: 46, show: false });
  });

  it('spans the period, with the midnights of Paris and now', () => {
    expect(option.xAxis).toMatchObject({ min: START, max: START + 9 * DAY });
    const lines = option.series[1].markLine?.data.map((line) => line.xAxis);
    expect(lines).toEqual([
      ...Array.from({ length: 8 }, (_, n) => START + (n + 1) * DAY),
      START + 7 * DAY + 12 * 3_600_000,
    ]);
  });

  it('shades the school holidays, the public holiday and tomorrow', () => {
    const areas = option.series[1].markArea?.data.map(([from, to]) => [from.xAxis, to.xAxis]);
    expect(areas).toEqual([
      [START + DAY, START + 3 * DAY],
      [START + 3 * DAY, START + 4 * DAY],
      [START + 8 * DAY, START + 9 * DAY],
    ]);
  });

  it('draws the line of zero when the scale holds it', () => {
    const [price] = PANELS;
    const withZero = panelOption(price, data, START, COLORS) as typeof option;
    const zero = withZero.series[0].markLine?.data.filter((line) => 'yAxis' in line);
    expect(zero).toEqual([{ yAxis: 0, lineStyle: { color: 'L', type: 'solid', width: 1 } }]);
    expect(option.series[1].markLine?.data.some((line) => 'yAxis' in line)).toBe(false);
  });

  it('leaves the cursor to the screen: the charts only draw', () => {
    expect(option.tooltip).toBeUndefined();
  });

  it('leaves now out when it is outside the period', () => {
    const later = panelOption(consumption, data, START + 10 * DAY, COLORS) as typeof option;
    expect(later.series[1].markLine?.data).toHaveLength(8);
  });
});
