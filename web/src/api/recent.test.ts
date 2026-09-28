// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import example from '../test/export-example.json';
import { fetchRecent, parseRecent, RecentError, SERIES_IDS, SOURCE_IDS } from './recent';

const START = 1_789_941_600_000; // Monday 21 September 2026, midnight in Paris
const DAY = 86_400_000;

/** An export of nine Paris days, as the API serves it, changed as asked. */
function exported(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    generated_at: START + 7 * DAY + 12 * 3_600_000,
    start: START,
    end: START + 9 * DAY,
    series: [
      {
        id: 'price',
        source: 'smard',
        unit: 'EUR/MWh',
        points: [
          [START, 81.5],
          [START + 900_000, -3],
        ],
      },
      { id: 'co2', source: 'eco2mix', unit: 'gCO2/kWh', points: [] },
    ],
    days: Array.from({ length: 9 }, (_, n) => ({
      day: `2026-09-${21 + n}`,
      start: START + n * DAY,
      end: START + (n + 1) * DAY,
      public_holiday: null,
      school_holidays: n === 3 ? 'Vacances de test' : null,
    })),
    sources: [
      {
        id: 'eco2mix',
        name: 'RTE, éCO2mix',
        licence: 'Licence Ouverte 2.0',
        updated_at: START + 7 * DAY,
        received_at: null,
      },
    ],
    ...changes,
  };
}

/** A fake fetch that answers once with this status and JSON body. */
const answering = (status: number, body: unknown) =>
  vi.fn<typeof fetch>().mockResolvedValue(Response.json(body, { status }));

describe('parseRecent', () => {
  it('accepts the example export, which names every series and every source', () => {
    // The API accepts the same file (tests/test_api.py).
    const recent = parseRecent(example);
    expect([...(recent?.series.keys() ?? [])]).toEqual(SERIES_IDS);
    expect([...(recent?.sources.keys() ?? [])]).toEqual(SOURCE_IDS);
  });

  it('says where it refuses an export, in the names of the shape only', () => {
    const places: string[] = [];
    const refused = (where: string) => {
      places.push(where);
    };
    const days = exported().days as Record<string, unknown>[];
    days[4].start = START;
    for (const value of [
      'Vacances',
      exported({
        series: [
          { id: 'co2', points: [] },
          { id: '<b>wind</b>', points: [] },
        ],
      }),
      exported({
        series: [
          {
            id: 'co2',
            points: [
              [START, 1],
              [START, 2],
            ],
          },
        ],
      }),
      exported({ days }),
      exported({ sources: [{ id: 'smard', updated_at: 'yesterday', received_at: null }] }),
    ]) {
      expect(parseRecent(value, refused)).toBeNull();
    }
    expect(places).toEqual([
      'the answer',
      'series[1].id',
      'series[0].points[1]',
      'days[4]',
      'sources[0]',
    ]);
  });

  it('keeps the series, the days and the dates of each source', () => {
    const recent = parseRecent(exported());
    expect(recent).not.toBeNull();
    expect(recent?.series.get('price')).toEqual([
      [START, 81.5],
      [START + 900_000, -3],
    ]);
    expect(recent?.series.get('co2')).toEqual([]);
    expect(recent?.days).toHaveLength(9);
    expect(recent?.days[3]).toEqual({
      day: '2026-09-24',
      start: START + 3 * DAY,
      end: START + 4 * DAY,
      publicHoliday: null,
      schoolHolidays: 'Vacances de test',
    });
    expect(recent?.sources.get('eco2mix')).toEqual({
      updatedAt: START + 7 * DAY,
      receivedAt: null,
    });
  });

  it('takes the control and format characters out of a name, and bounds it', () => {
    const days = exported().days as Record<string, unknown>[];
    days[0].public_holiday = 'Jour\u0000 de‮ test';
    days[1].school_holidays = 'É'.repeat(300);
    days[2].public_holiday = '​';
    const recent = parseRecent(exported({ days }));
    expect(recent?.days[0].publicHoliday).toBe('Jour de test');
    expect(recent?.days[1].schoolHolidays).toHaveLength(100);
    expect(recent?.days[1].schoolHolidays?.endsWith('…')).toBe(true);
    expect(recent?.days[2].publicHoliday).toBeNull();
  });

  it.each([
    ['not an object', []],
    ['a period in words', exported({ start: '2026-09-21' })],
    ['a period that ends before it starts', exported({ end: START - DAY, days: [] })],
    ['a period of eleven days', exported({ end: START + 11 * DAY, days: [] })],
    ['an unknown series', exported({ series: [{ id: 'wind', points: [] }] })],
    [
      'a series named twice',
      exported({
        series: [
          { id: 'co2', points: [] },
          { id: 'co2', points: [] },
        ],
      }),
    ],
    ['a value in words', exported({ series: [{ id: 'co2', points: [[START, '81.5']] }] })],
    ['a value that is not finite', exported({ series: [{ id: 'co2', points: [[START, null]] }] })],
    [
      'points out of order',
      exported({
        series: [
          {
            id: 'co2',
            points: [
              [START + 900_000, 1],
              [START, 2],
            ],
          },
        ],
      }),
    ],
    [
      'a point after the period',
      exported({ series: [{ id: 'co2', points: [[START + 9 * DAY, 1]] }] }),
    ],
    ['a day of another shape', exported({ days: [{ day: '21/09/2026' }] })],
    [
      'days with a gap',
      exported({
        days: (exported().days as Record<string, unknown>[]).filter((_, n) => n !== 4),
      }),
    ],
    ['an unknown source', exported({ sources: [{ id: 'elsewhere' }] })],
    [
      'a date that is not an instant',
      exported({ sources: [{ id: 'smard', updated_at: 1.5, received_at: null }] }),
    ],
    // Past 8.64e15 ms, a date no longer formats: the API refuses anything from 2100 on too.
    [
      'a date in 2100',
      exported({ sources: [{ id: 'smard', updated_at: 4_102_444_800_000, received_at: null }] }),
    ],
  ])('refuses %s', (_, value) => {
    expect(parseRecent(value)).toBeNull();
  });
});

describe('fetchRecent', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('asks /api/data/recent, lets the browser check its copy, and passes the signal on', async () => {
    const fakeFetch = answering(200, exported());
    vi.stubGlobal('fetch', fakeFetch);
    const { signal } = new AbortController();

    await expect(fetchRecent(signal)).resolves.toMatchObject({ start: START });
    const [url, init] = fakeFetch.mock.calls[0];
    expect(url).toBe('/api/data/recent');
    expect(init?.headers).toEqual({ Accept: 'application/json' });
    expect(init?.cache).toBe('no-cache');
    expect(init?.signal).toBe(signal);
  });

  it('says so when there is no export yet', async () => {
    vi.stubGlobal('fetch', answering(503, { detail: 'No export of the recent days yet' }));
    await expect(fetchRecent()).rejects.toEqual(new RecentError({ kind: 'none' }));
  });

  it.each([
    [503, { detail: 'The export of the recent days is unusable' }],
    [502, 'Bad gateway'],
  ])('reports another error status %i with its code', async (status, body) => {
    vi.stubGlobal('fetch', answering(status, body));
    await expect(fetchRecent()).rejects.toMatchObject({ failure: { kind: 'http', status } });
  });

  it('reports an answer that is not an export, and where it is refused', async () => {
    vi.stubGlobal('fetch', answering(200, { status: 'ok' }));
    await expect(fetchRecent()).rejects.toMatchObject({
      failure: { kind: 'format' },
      cause: { message: 'The export is refused at generated_at' },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(new Response('<html>', { status: 200 })),
    );
    await expect(fetchRecent()).rejects.toMatchObject({ failure: { kind: 'format' } });
  });
});
