import { useEffect } from 'react';
import styles from './App.module.css';
import { DataScreen } from './components/DataScreen';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Message } from './components/Message';
import { StatusScreen } from './components/StatusScreen';
import { TopBar } from './components/TopBar';
import { TEXT } from './text';
import { useScreen } from './useScreen';

export default function App() {
  const screen = useScreen();
  useEffect(() => {
    document.title = `${screen === 'data' ? TEXT.data.title : TEXT.statusTitle} · Ampère`;
  }, [screen]);
  return (
    <div className={styles.app}>
      <TopBar screen={screen} />
      <main className={styles.main}>
        {/* A screen that fails leaves the bar at the top, and the way to the other screen. */}
        <ErrorBoundary key={screen} fallback={<Message {...TEXT.screenFailed} />}>
          {screen === 'data' ? <DataScreen /> : <StatusScreen />}
        </ErrorBoundary>
      </main>
    </div>
  );
}
