import { Link } from 'react-router';
import styles from './StubPage.module.css';

// Заготовка экрана ленты. Лента, фильтры и выбор клиента — следующий шаг.
export function AnalyticsPage() {
  return (
    <div className={styles.page}>
      <h1 className={styles.title}>Персонализированная новостная аналитика</h1>
      <p className={styles.text}>
        Здесь будет лента событий под выбранного клиента: фильтры по отрасли, типу риска и источнику,
        карточки с интерпретацией и метками «факт / банк / AI».
      </p>
      <Link to="/" className={styles.back}>
        ← На главную
      </Link>
    </div>
  );
}
