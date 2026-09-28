import { useApiHealth } from '../api/useApiHealth';
import { TEXT } from '../text';
import { ApiStatusCard } from './ApiStatusCard';
import styles from './StatusScreen.module.css';

/** The state of the API, checked again every REFRESH_MS while this screen shows. */
export function StatusScreen() {
  const health = useApiHealth();
  return (
    <>
      <div className={styles.heading}>
        <h1 className={styles.title}>{TEXT.statusTitle}</h1>
        <p className={styles.intro}>{TEXT.statusIntro}</p>
      </div>
      <ApiStatusCard health={health} />
    </>
  );
}
