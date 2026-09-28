/** How the dashboard writes instants: Paris time, as the project shows every instant, whatever
 * the time zone of the browser. */
const PARIS = 'Europe/Paris';

/** "Mon 21": the axis of the Data screen. */
export const dayLabel = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: 'numeric',
  timeZone: PARIS,
});

/** "M 21": the same axis, on a narrow screen. */
export const narrowDayLabel = new Intl.DateTimeFormat('en-GB', {
  weekday: 'narrow',
  day: 'numeric',
  timeZone: PARIS,
});

/** "23:45": the time of a latest value. */
export const clockLabel = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: PARIS,
});

/** "Sat 26 Sep, 19:00": the instant under the cursor, or a row of the table. */
export const momentLabel = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: PARIS,
});

/** "28 Sep 2026": the date a source published for its last update. */
export const dateLabel = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: PARIS,
});

/** A value with a fixed number of decimals, and a real minus sign. */
export function number(value: number, digits: number): string {
  return value
    .toLocaleString('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    .replace('-', '−');
}
