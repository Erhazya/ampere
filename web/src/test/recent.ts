import { parseRecent, type Recent } from '../api/recent';

/** Monday 21 September 2026, midnight in Paris, and a day. */
export const START = 1_789_941_600_000;
export const DAY = 86_400_000;
export const QUARTER = 900_000;

/** An export of nine Paris days as the API serves it: quarter-hourly series, a school holiday
 * over two days and a public holiday on the fourth, changed as asked. */
export function exported(changes: Record<string, unknown> = {}): Record<string, unknown> {
  const quarters = (count: number, value: (n: number) => number) =>
    Array.from({ length: count }, (_, n) => [START + n * QUARTER, value(n)]);
  return {
    generated_at: START + 7 * DAY + 10 * 3_600_000,
    start: START,
    end: START + 9 * DAY,
    series: [
      {
        id: 'price',
        source: 'smard',
        unit: 'EUR/MWh',
        points: quarters(8 * 96, (n) => 80 + (n % 96)),
      },
      { id: 'consumption', source: 'eco2mix', unit: 'MW', points: quarters(7 * 96, () => 45_900) },
      { id: 'rte_forecast', source: 'eco2mix', unit: 'MW', points: quarters(9 * 96, () => 42_700) },
      { id: 'co2', source: 'eco2mix', unit: 'gCO2/kWh', points: quarters(7 * 96, () => 44) },
      { id: 'solar', source: 'eco2mix', unit: 'MW', points: [] },
      { id: 'temperature', source: 'openmeteo', unit: 'degC', points: [[START, 15.5]] },
      { id: 'temperature_forecast', source: 'openmeteo', unit: 'degC', points: [] },
    ],
    days: Array.from({ length: 9 }, (_, n) => ({
      day: `2026-09-${21 + n}`,
      start: START + n * DAY,
      end: START + (n + 1) * DAY,
      public_holiday: n === 3 ? 'Jour de test' : null,
      school_holidays: n === 1 || n === 2 ? 'Vacances de test' : null,
    })),
    sources: [
      {
        id: 'eco2mix',
        name: 'RTE, éCO2mix',
        licence: 'Licence Ouverte 2.0',
        updated_at: START + 7 * DAY + 9 * 3_600_000,
        received_at: START + 7 * DAY + 9 * 3_600_000 + 360_000,
      },
      {
        id: 'smard',
        name: 'Bundesnetzagentur | SMARD.de',
        licence: 'CC BY 4.0',
        updated_at: null,
        received_at: START + 6 * DAY,
      },
    ],
    ...changes,
  };
}

/** The same export, as the dashboard keeps it once checked. */
export function recent(changes: Record<string, unknown> = {}): Recent {
  const value = parseRecent(exported(changes));
  if (value === null) throw new Error('the test export is not an export');
  return value;
}
