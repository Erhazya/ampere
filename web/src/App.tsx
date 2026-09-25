import { useApiHealth } from './api/useApiHealth';
import styles from './App.module.css';
import { ApiStatusCard } from './components/ApiStatusCard';
import { TopBar } from './components/TopBar';
import { TEXT } from './text';

export default function App() {
  const health = useApiHealth();
  return (
    <div className={styles.app}>
      <TopBar />
      <main className={styles.main}>
        <div className={styles.heading}>
          <h1 className={styles.title}>{TEXT.statusTitle}</h1>
          <p className={styles.intro}>{TEXT.statusIntro}</p>
        </div>
        <ApiStatusCard health={health} />
      </main>
    </div>
  );
}
