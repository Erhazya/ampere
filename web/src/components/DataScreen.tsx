import { lazy, Suspense, useState } from 'react';
import { useRecent } from '../api/useRecent';
import { TEXT } from '../text';
import styles from './DataScreen.module.css';
import { DataTable } from './DataTable';
import { Sources } from './Sources';

type View = 'chart' | 'table';

// ECharts comes in a file of its own, fetched with the recent days: the rest of the screen shows
// at once, and the state of the API never loads it.
const DataCharts = lazy(() =>
  import('./DataCharts').then((module) => ({ default: module.DataCharts })),
);

/**
 * The Data screen (ADR 029): the last seven days in Paris time, today and tomorrow, as charts or
 * as a table, with the sources to cite. It asks the API once when it shows.
 */
export function DataScreen() {
  const recent = useRecent();
  const [view, setView] = useState<View>('chart');
  return (
    <>
      <div className={styles.heading}>
        <div className={styles.words}>
          <h1 className={styles.title}>{TEXT.data.title}</h1>
          <p className={styles.intro}>{TEXT.data.intro}</p>
        </div>
        {recent.state === 'ready' && <ViewToggle view={view} onChange={setView} />}
      </div>
      {recent.state === 'loading' && <Message {...TEXT.data.loading} />}
      {recent.state === 'failed' && (
        <Message {...(recent.failure.kind === 'none' ? TEXT.data.none : TEXT.data.unavailable)} />
      )}
      {recent.state === 'ready' && (
        <>
          <Legend />
          {view === 'chart' ? (
            <Suspense fallback={<Message {...TEXT.data.loading} />}>
              <DataCharts recent={recent.recent} />
            </Suspense>
          ) : (
            <DataTable recent={recent.recent} />
          )}
          <Sources recent={recent.recent} />
        </>
      )}
    </>
  );
}

function ViewToggle({ view, onChange }: { view: View; onChange: (view: View) => void }) {
  return (
    <div className={styles.toggle} role="group" aria-label={TEXT.data.views.label}>
      {(['chart', 'table'] as const).map((choice) => (
        <button
          key={choice}
          type="button"
          className={styles.choice}
          aria-pressed={view === choice}
          onClick={() => {
            onChange(choice);
          }}
        >
          {TEXT.data.views[choice]}
        </button>
      ))}
    </div>
  );
}

function Legend() {
  const { measured, forecast, tomorrow, now } = TEXT.data.legend;
  return (
    <ul className={styles.legend}>
      <li>
        <span className={styles.measured} aria-hidden="true" />
        {measured}
      </li>
      <li>
        <span className={styles.forecast} aria-hidden="true" />
        {forecast}
      </li>
      <li>
        <span className={styles.tomorrow} aria-hidden="true" />
        {tomorrow}
      </li>
      <li>
        <span className={styles.now} aria-hidden="true" />
        {now}
      </li>
    </ul>
  );
}

function Message({ title, detail }: { title: string; detail: string }) {
  return (
    <div className={styles.message} role="status">
      <p className={styles.messageTitle}>{title}</p>
      <p className={styles.messageDetail}>{detail}</p>
    </div>
  );
}
