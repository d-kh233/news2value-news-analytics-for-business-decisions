import { getInsightTrace } from '../../api/client';
import { useAsync } from '../../api/useAsync';
import type { TraceStep } from '../../types';
import { formatDate } from '../../utils/format';
import { roleLabel, sourceIdLabel } from '../../utils/labels';
import styles from './TracePanel.module.css';

const statusLabel: Record<string, string> = {
  ok: 'выполнено',
  fallback: 'запасной вариант',
  skipped: 'пропущено',
  error: 'ошибка',
};

const generatedByLabel: Record<string, string> = {
  llm: 'языковая модель',
  rules: 'правила агента (без модели)',
  fake: 'заглушка',
};

function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '';
  return ms >= 1000 ? `${(ms / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} с` : `${ms} мс`;
}

/** Данные шага для любопытных — JSON, обрезанный, чтобы не раздувать страницу. */
function StepData({ step }: { step: Partial<TraceStep> }) {
  const parts = [
    ['Вход', step.input],
    ['Результат', step.output],
  ].filter(([, value]) => value !== undefined && value !== null && JSON.stringify(value) !== '{}');
  if (parts.length === 0) return null;
  return (
    <details className={styles.data}>
      <summary>Данные шага</summary>
      {parts.map(([label, value]) => {
        const json = JSON.stringify(value, null, 2);
        return (
          <div key={label as string}>
            <p className={styles.dataLabel}>{label as string}</p>
            <pre className={styles.pre}>{json.length > 2000 ? `${json.slice(0, 2000)}\n…` : json}</pre>
          </div>
        );
      })}
    </details>
  );
}

/**
 * «Как сделан этот вывод» — трассировка (критерий 12): запуск агента, шаги со временем,
 * документы-источники, признаки компании и версия промптов (GET /insights/{id}/trace).
 */
export function TracePanel({ insightId }: { insightId: string }) {
  const state = useAsync(() => getInsightTrace(insightId), [insightId]);

  if (state.status === 'loading') return null;
  if (state.status === 'error') {
    return (
      <section className={styles.panel}>
        <p className={styles.muted}>Трассировка этой карточки недоступна.</p>
      </section>
    );
  }

  const trace = state.data;
  const steps = trace.steps;
  const features = trace.used_profile_fields ?? [];

  return (
    <section className={styles.panel} aria-labelledby="trace-title">
      <details>
        <summary className={styles.summary}>
          <span id="trace-title" className={styles.title}>
            Как сделан этот вывод
          </span>
          <span className={styles.muted}>
            {steps.length > 0
              ? `${steps.length} шагов агента${trace.duration_ms ? ` · ${formatMs(trace.duration_ms)}` : ''}`
              : 'демо-данные без запуска агента'}
          </span>
        </summary>

        <div className={styles.body}>
          <dl className={styles.meta}>
            {trace.role && (
              <>
                <dt>Для кого</dt>
                <dd>{roleLabel[trace.role as keyof typeof roleLabel] ?? trace.role}</dd>
              </>
            )}
            {trace.generated_by && (
              <>
                <dt>Кто написал текст</dt>
                <dd>{generatedByLabel[trace.generated_by] ?? trace.generated_by}</dd>
              </>
            )}
            {trace.llm_mode && (
              <>
                <dt>Режим модели</dt>
                <dd>{trace.llm_mode}</dd>
              </>
            )}
            {trace.prompt_version && (
              <>
                <dt>Версия промпта</dt>
                <dd>{trace.prompt_version}</dd>
              </>
            )}
            {trace.finished_at && (
              <>
                <dt>Когда</dt>
                <dd>{formatDate(trace.finished_at)}</dd>
              </>
            )}
            {trace.generator_run_id && (
              <>
                <dt>Запуск</dt>
                <dd className={styles.code}>{trace.generator_run_id}</dd>
              </>
            )}
          </dl>

          {steps.length > 0 ? (
            <ol className={styles.steps}>
              {steps.map((step, index) => {
                const status = step.status ?? 'ok';
                return (
                  <li key={index} className={styles.step}>
                    <div className={styles.stepHead}>
                      <span className={styles.stepNumber}>{step.step ?? index + 1}</span>
                      <span className={styles.stepName}>{step.name ?? 'Шаг'}</span>
                      <span className={`${styles.status} ${styles[`status-${status}`] ?? ''}`}>
                        {statusLabel[status] ?? status}
                      </span>
                      {step.duration_ms !== undefined && step.duration_ms !== null && (
                        <span className={styles.muted}>{formatMs(step.duration_ms)}</span>
                      )}
                    </div>
                    {step.tool && <p className={styles.tool}>инструмент: {step.tool}</p>}
                    {step.note && <p className={styles.note}>{step.note}</p>}
                    <StepData step={step} />
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className={styles.muted}>
              Эта карточка из демо-набора и не создавалась агентом. Нажмите «Обновить анализ» в ленте — у новых
              карточек будет полная трассировка.
            </p>
          )}

          {features.length > 0 && (
            <p className={styles.features}>
              <span className={styles.featuresLabel}>Признаки компании:</span>{' '}
              {[...new Set(features.map(sourceIdLabel))].join(' · ')}
            </p>
          )}

          {trace.sources.length > 0 && (
            <ul className={styles.sources}>
              {trace.sources.map((source, index) => (
                <li key={`${source.url}-${index}`}>
                  <span className={styles.muted}>
                    {source.source_kind === 'bank_analytics' ? 'Аналитика банка' : 'Новость'}
                    {source.source_name ? ` · ${source.source_name}` : ''}
                  </span>{' '}
                  {source.title}
                </li>
              ))}
            </ul>
          )}
        </div>
      </details>
    </section>
  );
}
