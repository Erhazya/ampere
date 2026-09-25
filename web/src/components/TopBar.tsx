import { TEXT } from '../text';
import styles from './TopBar.module.css';

/** Name of the app, at the top of every screen. */
export function TopBar() {
  return (
    <header className={styles.bar}>
      <span className={styles.mark} aria-hidden="true" />
      <span className={styles.name}>Ampère</span>
      <span className={styles.tagline}>{TEXT.tagline}</span>
    </header>
  );
}
