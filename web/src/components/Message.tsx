import type { ReactNode } from 'react';
import styles from './Message.module.css';

/** A short message in place of a screen or of its charts: what happens, then what to do. */
export function Message({
  title,
  detail,
  children,
}: {
  title: string;
  detail: string;
  children?: ReactNode;
}) {
  return (
    <div className={styles.message} role="status">
      <p className={styles.title}>{title}</p>
      <p className={styles.detail}>{detail}</p>
      {children}
    </div>
  );
}
