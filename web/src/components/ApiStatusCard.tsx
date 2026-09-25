import { HEALTH_URL } from '../api/health';
import type { ApiHealth } from '../api/useApiHealth';
import { TEXT } from '../text';
import styles from './ApiStatusCard.module.css';

/** Time of a check as the project shows it: Paris time, 24-hour clock. */
const clock = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
  timeZone: 'Europe/Paris',
});

type Kind = 'code' | 'reason';
interface Row {
  label: string;
  value: string;
  kind?: Kind;
}

/** The details worth showing for each state: nothing is known yet while checking. */
function rowsFor(health: ApiHealth): Row[] {
  const rows: Row[] = [];
  if (health.state === 'up')
    rows.push({ label: TEXT.labels.version, value: health.version, kind: 'code' });
  if (health.state === 'down')
    rows.push({ label: TEXT.labels.reason, value: health.reason, kind: 'reason' });
  if (health.state !== 'checking')
    rows.push({ label: TEXT.labels.lastCheck, value: clock.format(health.checkedAt) });
  rows.push({ label: TEXT.labels.endpoint, value: HEALTH_URL, kind: 'code' });
  return rows;
}

/**
 * The state of the API, in words and with an indicator, then its details. Only the
 * word is announced to screen readers when it changes, not the time of each check.
 */
export function ApiStatusCard({ health }: { health: ApiHealth }) {
  return (
    <div className={styles.card}>
      <p className={styles.state}>
        <span className={`${styles.indicator} ${styles[health.state]}`} aria-hidden="true" />
        <span role="status">{TEXT.states[health.state]}</span>
      </p>
      <hr className={styles.rule} />
      <dl className={styles.details}>
        {rowsFor(health).map(({ label, value, kind }) => (
          <div key={label} className={styles.row}>
            <dt className={styles.label}>{label}</dt>
            <dd className={kind ? `${styles.value} ${styles[kind]}` : styles.value}>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
