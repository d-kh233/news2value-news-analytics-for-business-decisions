import { useMemo, useState } from 'react';
import { listInsights } from '../api/client';
import { useAsync } from '../api/useAsync';
import { FiltersPanel } from '../components/feed/FiltersPanel';
import { InsightCard } from '../components/feed/InsightCard';
import { RefreshAgentButton } from '../components/feed/RefreshAgentButton';
import { useCurrentCompany } from '../state/CurrentCompany';
import { applyFilters, defaultFilters, type FeedFilters, type SortOrder } from '../utils/filters';
import { pluralEvents } from '../utils/format';
import styles from './AnalyticsPage.module.css';

/** Лента персонализированной аналитики выбранной компании. */
export function AnalyticsPage() {
  const { company, error: companiesError, insightsVersion } = useCurrentCompany();
  const [filters, setFilters] = useState<FeedFilters>(defaultFilters);
  const state = useAsync(
    () => (company ? listInsights(company.id, { limit: 100 }) : new Promise<never>(() => {})),
    [company?.id, insightsVersion],
  );

  const insights = state.status === 'success' ? state.data.items : [];
  const visible = useMemo(() => applyFilters(insights, filters), [insights, filters]);

  const error = companiesError ?? (state.status === 'error' ? state.error : null);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Персонализированная новостная аналитика</h1>
          {company && (
            <p className={styles.subtitle}>
              {company.name} · {company.industry} · {company.region} · выручка{' '}
              {company.revenue_bln_rub.toLocaleString('ru-RU')} млрд ₽
            </p>
          )}
        </div>
        <RefreshAgentButton />
      </header>

      <div className={styles.layout}>
        <FiltersPanel filters={filters} onChange={setFilters} />

        <section className={styles.feed} aria-label="Лента событий">
          <div className={styles.toolbar}>
            <p className={styles.count} aria-live="polite">
              {state.status === 'success' ? `Найдено: ${pluralEvents(visible.length)}` : ''}
            </p>
            <label className={styles.sort}>
              <span>Сортировка</span>
              <select
                value={filters.sort}
                onChange={(event) => setFilters({ ...filters, sort: event.target.value as SortOrder })}
              >
                <option value="importance">Сначала важные</option>
                <option value="date">Сначала новые</option>
              </select>
            </label>
          </div>

          {error ? (
            <p className={styles.message} role="alert">
              Не удалось загрузить события. Проверьте, что бэкенд запущен, и обновите страницу.
            </p>
          ) : state.status !== 'success' ? (
            <p className={styles.message} role="status">
              Загружаем события…
            </p>
          ) : visible.length === 0 ? (
            <div className={styles.message}>
              <p>Под выбранные фильтры событий нет.</p>
              <button type="button" className={styles.resetButton} onClick={() => setFilters(defaultFilters)}>
                Сбросить фильтры
              </button>
            </div>
          ) : (
            <ul className={styles.list}>
              {visible.map((insight) => (
                <li key={insight.id}>
                  <InsightCard insight={insight} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
