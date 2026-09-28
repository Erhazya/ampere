import { TEXT } from '../text';
import type { Screen } from '../useScreen';
import styles from './TopBar.module.css';

const LINKS: { screen: Screen; href: string }[] = [
  { screen: 'data', href: '#/' },
  { screen: 'status', href: '#/status' },
];

/** Name of the app, at the top of every screen, and the way to each screen. */
export function TopBar({ screen }: { screen: Screen }) {
  return (
    <header className={styles.bar}>
      <span className={styles.mark} aria-hidden="true" />
      <span className={styles.name}>Ampère</span>
      <span className={styles.tagline}>{TEXT.tagline}</span>
      <nav className={styles.nav} aria-label={TEXT.screens.label}>
        {LINKS.map((link) => (
          <a
            key={link.screen}
            className={styles.link}
            href={link.href}
            aria-current={link.screen === screen ? 'page' : undefined}
          >
            {TEXT.screens[link.screen]}
          </a>
        ))}
      </nav>
    </header>
  );
}
