import { useEffect, useRef, useState } from 'react';
import { getCurrentUser, refreshInsights, waitForJob } from '../../api/client';
import { useAuth } from '../../state/Auth';
import { useCurrentCompany } from '../../state/CurrentCompany';
import { Icon } from '../ui/Icon';
import styles from './RefreshAgentButton.module.css';

type Status = { kind: 'idle' } | { kind: 'running' } | { kind: 'done' } | { kind: 'error'; message: string };

/**
 * Запуск AI-агента: он заново ищет новости, отбирает важные и пишет карточки из 6 блоков.
 * runningJobId — обновление, которое уже идёт на сервере (refresh_job_id из ленты): кнопка его подхватывает.
 */
export function RefreshAgentButton({ runningJobId }: { runningJobId?: string | null }) {
  const { company, notifyInsightsChanged, setLastRunJobId } = useCurrentCompany();
  const { user } = useAuth();
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  const watching = useRef<string | null>(null);

  async function follow(jobId: string) {
    if (watching.current === jobId) return;
    watching.current = jobId;
    setStatus({ kind: 'running' });
    try {
      const job = await waitForJob(jobId);
      if (job.status === 'failed') {
        setStatus({ kind: 'error', message: 'Агент не смог обновить анализ — показаны прежние данные.' });
        return;
      }
      notifyInsightsChanged();
      setStatus({ kind: 'done' });
    } catch {
      setStatus({ kind: 'error', message: 'Не удалось дождаться агента. Обновите страницу.' });
    } finally {
      watching.current = null;
    }
  }

  async function run() {
    if (!company) return;
    setStatus({ kind: 'running' });
    try {
      // Вход включён — сервер сам берёт роль пользователя; выключен — роль демо-пользователя из шапки
      const role = user.auth_enabled ? undefined : (await getCurrentUser()).roleKey;
      const { job_id } = await refreshInsights(company.id, role);
      setLastRunJobId(company.id, job_id);
      await follow(job_id);
    } catch {
      setStatus({ kind: 'error', message: 'Не удалось запустить анализ. Проверьте, что бэкенд запущен.' });
    }
  }

  useEffect(() => {
    if (runningJobId && status.kind !== 'running') void follow(runningJobId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningJobId]);

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
        {status.kind === 'running' && 'Ищет новости, сверяет с аналитикой банка и проверяет выводы. Это может занять минуту.'}
        {status.kind === 'done' && 'Готово: карточки обновлены'}
        {status.kind === 'error' && status.message}
      </p>
    </div>
  );
}
