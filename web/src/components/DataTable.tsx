import type { Recent, SeriesId } from '../api/recent';
import { momentLabel, number } from '../format';
import { PANELS, pointAt } from '../panels';
import { TEXT } from '../text';
import styles from './DataTable.module.css';

const HOUR = 3_600_000;

/** The columns of the table: each series, with the panel that gives its unit and decimals. */
const COLUMNS = PANELS.flatMap((panel) =>
  [panel.measured, ...(panel.forecast ? [panel.forecast] : [])].map((id: SeriesId) => ({
    id,
    panel,
    unit: TEXT.data.panels[panel.id as keyof typeof TEXT.data.panels].unit,
  })),
);

/**
 * The recent days hour by hour, in Paris time: what the charts show, for a screen reader, a
 * keyboard, or anyone who prefers numbers. Hours without any value are left out.
 */
export function DataTable({ recent }: { recent: Recent }) {
  const rows: { instant: number; values: (number | undefined)[] }[] = [];
  for (let instant = recent.start; instant < recent.end; instant += HOUR) {
    const values = COLUMNS.map(({ id }) => pointAt(recent.series.get(id), instant, 0)?.[1]);
    if (values.some((value) => value !== undefined)) rows.push({ instant, values });
  }
  return (
    <div className={styles.scroll} role="region" aria-label={TEXT.data.table} tabIndex={0}>
      <table className={styles.table}>
        <caption className={styles.caption}>{TEXT.data.table}</caption>
        <thead>
          <tr>
            <th scope="col">{TEXT.data.time}</th>
            {COLUMNS.map(({ id, unit }) => (
              <th key={id} scope="col">
                {TEXT.data.series[id]} <span className={styles.unit}>{unit}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ instant, values }) => (
            <tr key={instant}>
              <th scope="row">{momentLabel.format(instant)}</th>
              {values.map((value, index) => (
                <td key={COLUMNS[index].id}>
                  {value === undefined
                    ? TEXT.data.noValue
                    : number(value * COLUMNS[index].panel.scale, COLUMNS[index].panel.digits)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
