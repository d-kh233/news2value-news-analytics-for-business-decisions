import { Link, useParams } from 'react-router';
import { getInsight } from '../api/client';
import { useAsync } from '../api/useAsync';
import styles from './StubPage.module.css';

// Заготовка карточки события. Полная карточка с 6 блоками и чатом — следующий шаг (пт, 23.10).
export function InsightPage() {
  const { insightId = '' } = useParams();
  const state = useAsync(() => getInsight(insightId), [insightId]);

  return (
    <div className={styles.page}>
      <Link to="/analytics" className={styles.back}>
        ← К ленте
      </Link>
      {state.status === 'loading' && <p role="status">Загружаем событие…</p>}
      {state.status === 'error' && <p role="alert">Событие не найдено.</p>}
      {state.status === 'success' && (
        <>
          <h1 className={styles.title}>{state.data.title}</h1>
          <p className={styles.text}>{state.data.summary}</p>
          <p className={styles.text}>Подробная карточка с шестью блоками появится на следующем шаге.</p>
        </>
      )}
    </div>
  );
}
