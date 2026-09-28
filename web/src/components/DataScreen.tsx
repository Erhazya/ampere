import { lazy, Suspense, useState } from 'react';
import type { Recent } from '../api/recent';
import { useRecent } from '../api/useRecent';
import { momentLabel } from '../format';
import { recentReason, TEXT } from '../text';
import styles from './DataScreen.module.css';
import { DataTable } from './DataTable';
import { ErrorBoundary } from './ErrorBoundary';
import { Message } from './Message';
import { Sources } from './Sources';

type View = 'chart' | 'table';

/** Past this age, the export is late: the daily job writes one every day at about 14:00. */
const STALE_MS = 26 * 3_600_000;

// ECharts comes in a file of its own, fetched with the recent days: the rest of the screen shows
// at once, and the state of the API never loads it.
const DataCharts = lazy(() =>
  import('./DataCharts').then((module) => ({ default: module.DataCharts })),
);

/**
 * The Data screen (ADR 029): the last seven days in Paris time, today and tomorrow, as charts or
 * as a table, with the sources to cite. It asks the API once when it shows, and again on demand
 * after a failure.
 */
export function DataScreen() {
  const [recent, retry] = useRecent();
  const [view, setView] = useState<View>('chart');
  // The moment of the visit, read once: the line of now and the days of the dates depend on it.
  const [now] = useState(() => Date.now());
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
      {recent.state === 'failed' &&
        (recent.failure.kind === 'none' ? (
          <Message {...TEXT.data.none} />
        ) : (
          <Message title={TEXT.data.unavailable} detail={recentReason(recent.failure)}>
            <button type="button" className={styles.retry} onClick={retry}>
              {TEXT.data.retry}
            </button>
          </Message>
        ))}
      {recent.state === 'ready' && (
        <>
          {now - recent.recent.generatedAt > STALE_MS && (
            <p className={styles.stale}>
              {`${TEXT.data.stale} ${momentLabel.format(recent.recent.generatedAt)}.`}
            </p>
          )}
          <Legend recent={recent.recent} now={now} />
          {view === 'chart' ? (
            <ErrorBoundary fallback={<Message {...TEXT.data.chartsFailed} />}>
              <Suspense fallback={<Message {...TEXT.data.chartsLoading} />}>
                <DataCharts recent={recent.recent} now={now} />
              </Suspense>
            </ErrorBoundary>
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

/** What the marks of the charts mean: now only when its line is drawn, within the period. */
function Legend({ recent, now }: { recent: Recent; now: number }) {
  const { measured, forecast, tomorrow, now: nowWord } = TEXT.data.legend;
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
      {recent.start < now && now < recent.end && (
        <li>
          <span className={styles.now} aria-hidden="true" />
          {nowWord}
        </li>
      )}
    </ul>
  );
}
