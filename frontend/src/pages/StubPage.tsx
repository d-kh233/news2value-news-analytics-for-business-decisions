import { Link } from 'react-router';
import styles from './StubPage.module.css';

// Разделы банка, которые в прототипе не реализуются.
export function StubPage({ title }: { title: string }) {
  return (
    <div className={styles.page}>
      <h1 className={styles.title}>{title}</h1>
      <p className={styles.text}>Этот раздел интернет-банка не входит в прототип.</p>
      <Link to="/" className={styles.back}>
        ← На главную
      </Link>
    </div>
  );
}
