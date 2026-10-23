import { useState } from 'react';
import { getCurrentUser, refreshInsights, waitForJob } from '../../api/client';
import { useCurrentCompany } from '../../state/CurrentCompany';
import { Icon } from '../ui/Icon';
import styles from './RefreshAgentButton.module.css';

type Status = { kind: 'idle' } | { kind: 'running' } | { kind: 'done' } | { kind: 'error'; message: string };

/** Запуск AI-агента: он заново ищет новости, отбирает важные и пишет карточки из 6 блоков. */
export function RefreshAgentButton() {
  const { company, notifyInsightsChanged, setLastRunJobId } = useCurrentCompany();
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  async function run() {
    if (!company) return;
    setStatus({ kind: 'running' });
    try {
      const user = await getCurrentUser();
      const { job_id } = await refreshInsights(company.id, user.roleKey);
      setLastRunJobId(company.id, job_id);
      const job = await waitForJob(job_id);
      if (job.status === 'failed') {
        setStatus({ kind: 'error', message: 'Агент не смог обновить анализ — показаны прежние данные.' });
        return;
      }
      notifyInsightsChanged();
      setStatus({ kind: 'done' });
    } catch {
      setStatus({ kind: 'error', message: 'Не удалось запустить анализ. Проверьте, что бэкенд запущен.' });
    }
  }

  const running = status.kind === 'running';

  return (
    <div className={styles.wrap}>
      <button type="button" className={styles.button} onClick={run} disabled={running || !company}>
        <span className={running ? styles.spinning : undefined}>
          <Icon name="spark" size={18} />
        </span>
        {running ? 'Агент анализирует…' : 'Обновить анализ'}
      </button>
      <p className={styles.status} role="status" aria-live="polite">
        {status.kind === 'running' && 'Ищет новости, сверяет с аналитикой банка и проверяет выводы'}
        {status.kind === 'done' && 'Готово: карточки обновлены'}
        {status.kind === 'error' && status.message}
      </p>
    </div>
  );
}
