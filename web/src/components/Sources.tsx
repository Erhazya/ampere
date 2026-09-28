import { type Recent, SOURCE_IDS } from '../api/recent';
import { dateLabel, momentLabel } from '../format';
import { TEXT } from '../text';
import styles from './Sources.module.css';

/**
 * Each source with its licence, the date it published for its last update when its licence
 * asks for it, and when Ampère received its last values; a date the export lacks is said to be
 * missing. The names and licences are written in the code, never read from the export (ADR 029).
 */
export function Sources({ recent }: { recent: Recent }) {
  return (
    <footer className={styles.sources}>
      <h2 className={styles.heading}>{TEXT.data.sources}</h2>
      <ul className={styles.list}>
        {SOURCE_IDS.map((id) => {
          const { name, licence } = TEXT.sources[id];
          const dates = recent.sources.get(id);
          return (
            <li key={id}>
              {name} ·{' '}
              <a href={TEXT.licences[licence]} rel="license noreferrer" target="_blank">
                {licence}
              </a>
              {dates?.updatedAt != null
                ? ` · ${TEXT.data.updated} ${dateLabel.format(dates.updatedAt)}`
                : licence === 'Licence Ouverte 2.0' && ` · ${TEXT.data.updateUnknown}`}
              {dates?.receivedAt != null
                ? ` · ${TEXT.data.received} ${momentLabel.format(dates.receivedAt)}`
                : ` · ${TEXT.data.nothingReceived}`}
            </li>
          );
        })}
      </ul>
      <p className={styles.note}>
        {TEXT.data.exportOf} {momentLabel.format(recent.generatedAt)}. {TEXT.data.footer}
      </p>
    </footer>
  );
}
