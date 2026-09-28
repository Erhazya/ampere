import { RECENT_URL } from './config';

/** The series of the Data screen, as the export names them (ADR 029, src/ampere/recent.py). */
export const SERIES_IDS = [
  'price',
  'consumption',
  'rte_forecast',
  'co2',
  'solar',
  'temperature',
  'temperature_forecast',
] as const;
export type SeriesId = (typeof SERIES_IDS)[number];

/** The sources the screen cites. Their names and licences are written in text.ts, never read. */
export const SOURCE_IDS = [
  'smard',
  'eco2mix',
  'openmeteo',
  'school-holidays',
  'public-holidays',
] as const;
export type SourceId = (typeof SOURCE_IDS)[number];

/** An instant in milliseconds since 1970, and a value. */
export type Point = readonly [number, number];

/** A Paris day of the period, from its midnight to the next one. */
export interface Day {
  day: string;
  start: number;
  end: number;
  publicHoliday: string | null;
  schoolHolidays: string | null;
}

/** When a source last updated its data, if its licence asks to say so, and when Ampère got it. */
export interface SourceDates {
  updatedAt: number | null;
  receivedAt: number | null;
}

/** The recent days, as the dashboard keeps them once checked. */
export interface Recent {
  generatedAt: number;
  start: number;
  end: number;
  series: ReadonlyMap<SeriesId, readonly Point[]>;
  days: readonly Day[];
  sources: ReadonlyMap<SourceId, SourceDates>;
}

/** Why the recent days are not there, in terms the interface puts into words (text.ts). */
export type RecentFailure =
  | { kind: 'none' }
  | { kind: 'http'; status: number }
  | { kind: 'format' }
  | { kind: 'timeout' }
  | { kind: 'network' }
  | { kind: 'unexpected' };

/** The error thrown for a failure found in the answer itself. */
export class RecentError extends Error {
  readonly failure: RecentFailure;

  constructor(failure: RecentFailure, options?: ErrorOptions) {
    super(failure.kind === 'http' ? `HTTP status ${failure.status}` : failure.kind, options);
    this.name = 'RecentError';
    this.failure = failure;
  }
}

/** The answer of the API before its first export (src/ampere/api.py). */
const NO_EXPORT = 'No export of the recent days yet';
/** The longest period an export covers, and the most points in a series (src/ampere/recent.py). */
const LONGEST_MS = 10 * 86_400_000;
const MAX_POINTS = 1_000;
const LABEL = 100;

/**
 * Asks the API for the recent days. The browser checks its copy against the ETag of the API,
 * so an export that did not change is not downloaded again. Rejects with a RecentError when
 * there is no export yet, when the answer has another error status, or when it is not an
 * export; otherwise with the error of fetch itself.
 */
export async function fetchRecent(signal?: AbortSignal): Promise<Recent> {
  const response = await fetch(RECENT_URL, {
    headers: { Accept: 'application/json' },
    cache: 'no-cache',
    signal,
  });
  if (!response.ok) {
    const detail = response.status === 503 ? await detailOf(response) : undefined;
    throw new RecentError(
      detail === NO_EXPORT ? { kind: 'none' } : { kind: 'http', status: response.status },
    );
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    if (error instanceof SyntaxError) throw new RecentError({ kind: 'format' }, { cause: error });
    throw error;
  }
  let where = '';
  const recent = parseRecent(body, (place) => {
    where = place;
  });
  if (recent === null) {
    const cause = new Error(`The export is refused at ${where}`);
    throw new RecentError({ kind: 'format' }, { cause });
  }
  return recent;
}

/** The detail of an error answer of FastAPI, if it has one. */
async function detailOf(response: Response): Promise<string | undefined> {
  try {
    const body: unknown = await response.json();
    return isObject(body) && typeof body.detail === 'string' ? body.detail : undefined;
  } catch {
    return undefined;
  }
}

/**
 * The recent days, checked at run time as the security review of ADR 029 asks: finite numbers,
 * names from closed lists, points in the order of time within the period, days that tile it.
 * Anything else in the answer is ignored. Null when the answer is not an export: `refused` then
 * learns where, in the names of the shape only, never in the words of the answer.
 */
export function parseRecent(value: unknown, refused?: (where: string) => void): Recent | null {
  const refuse = (where: string): null => {
    refused?.(where);
    return null;
  };
  if (!isObject(value)) return refuse('the answer');
  const { generated_at: generatedAt, start, end } = value;
  if (!isInstant(generatedAt)) return refuse('generated_at');
  if (!isInstant(start)) return refuse('start');
  if (!isInstant(end) || !(start < end && end <= start + LONGEST_MS)) return refuse('end');

  if (!Array.isArray(value.series)) return refuse('series');
  const series = new Map<SeriesId, readonly Point[]>();
  for (const [n, item] of (value.series as unknown[]).entries()) {
    if (!isObject(item) || !isOneOf(item.id, SERIES_IDS) || series.has(item.id))
      return refuse(`series[${n}].id`);
    const points = pointsOf(item.points, start, end, `series[${n}].points`, refuse);
    if (points === null) return null;
    series.set(item.id, points);
  }

  if (!Array.isArray(value.days) || value.days.length > 12) return refuse('days');
  const days: Day[] = [];
  let edge = start;
  for (const [n, item] of (value.days as unknown[]).entries()) {
    if (
      !isObject(item) ||
      typeof item.day !== 'string' ||
      !/^\d{4}-\d{2}-\d{2}$/.test(item.day) ||
      item.start !== edge ||
      !isInstant(item.end) ||
      item.end <= edge
    )
      return refuse(`days[${n}]`);
    days.push({
      day: item.day,
      start: edge,
      end: item.end,
      publicHoliday: nameOf(item.public_holiday),
      schoolHolidays: nameOf(item.school_holidays),
    });
    edge = item.end;
  }
  if (days.length > 0 && edge !== end) return refuse('days');

  if (!Array.isArray(value.sources)) return refuse('sources');
  const sources = new Map<SourceId, SourceDates>();
  for (const [n, item] of (value.sources as unknown[]).entries()) {
    if (!isObject(item) || !isOneOf(item.id, SOURCE_IDS) || sources.has(item.id))
      return refuse(`sources[${n}].id`);
    const { updated_at: updatedAt, received_at: receivedAt } = item;
    if (!isInstantOrNull(updatedAt) || !isInstantOrNull(receivedAt)) return refuse(`sources[${n}]`);
    sources.set(item.id, { updatedAt, receivedAt });
  }
  return { generatedAt, start, end, series, days, sources };
}

/** The points of a series, or null, once `refuse` has learnt the first one refused. */
function pointsOf(
  value: unknown,
  start: number,
  end: number,
  where: string,
  refuse: (where: string) => null,
): Point[] | null {
  if (!Array.isArray(value) || value.length > MAX_POINTS) return refuse(where);
  const points: Point[] = [];
  let last = -Infinity;
  for (const [n, point] of (value as unknown[]).entries()) {
    if (!Array.isArray(point) || point.length !== 2) return refuse(`${where}[${n}]`);
    const [instant, measured] = point as unknown[];
    if (
      !isInstant(instant) ||
      typeof measured !== 'number' ||
      !Number.isFinite(measured) ||
      instant <= last ||
      instant < start ||
      instant >= end
    )
      return refuse(`${where}[${n}]`);
    points.push([instant, measured]);
    last = instant;
  }
  return points;
}

/** A third-party name as text: without control or format characters, and bounded. */
function nameOf(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const kept = value.replace(/[\p{Cc}\p{Cf}]/gu, '').trim();
  if (kept === '') return null;
  return kept.length <= LABEL ? kept : `${kept.slice(0, LABEL - 1)}…`;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** An instant from 2000 to 2100, as the API's shape has it (Millis in src/ampere/recent.py). */
function isInstant(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= 946_684_800_000 &&
    value < 4_102_444_800_000
  );
}

function isInstantOrNull(value: unknown): value is number | null {
  return value === null || isInstant(value);
}

function isOneOf<T extends string>(value: unknown, names: readonly T[]): value is T {
  return typeof value === 'string' && (names as readonly string[]).includes(value);
}
