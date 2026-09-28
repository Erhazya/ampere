import { useEffect } from 'react';
import styles from './App.module.css';
import { DataScreen } from './components/DataScreen';
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
      <main className={styles.main}>{screen === 'data' ? <DataScreen /> : <StatusScreen />}</main>
    </div>
  );
}
