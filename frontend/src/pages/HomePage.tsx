import { getAccounts, getCompanyProfile, getCurrentUser, listInsights } from '../api/client';
import { useAsync } from '../api/useAsync';
import { AccountsWidget, QuickActions } from '../components/home/AccountsWidget';
import { InsightsEntryCard } from '../components/home/InsightsEntryCard';
import { useAuth } from '../state/Auth';
import { useCurrentCompany } from '../state/CurrentCompany';
import type { Company } from '../types';
import { formatToday } from '../utils/format';
import { topByImportance } from '../utils/insight';
import styles from './HomePage.module.css';

async function loadHome(company: Company) {
  const [user, accounts, profile, insights] = await Promise.all([
    getCurrentUser(),
    getAccounts(),
    getCompanyProfile(company.id),
    listInsights(company.id, { limit: 100 }),
  ]);
  return { user, accounts, profile, insights };
}

export function HomePage() {
  const { company, error: companiesError, insightsVersion } = useCurrentCompany();
  const auth = useAuth();
  const state = useAsync(
    () => (company ? loadHome(company) : new Promise<never>(() => {})),
    [company?.id, insightsVersion],
  );

  const error = companiesError ?? (state.status === 'error' ? state.error : null);
  if (error) {
    return (
      <p className={styles.status} role="alert">
        Не удалось загрузить данные. Проверьте, что бэкенд запущен, и обновите страницу.
      </p>
    );
  }

  if (!company || state.status !== 'success') {
    return (
      <p className={styles.status} role="status">
        Загружаем данные…
      </p>
    );
  }

  const { user, accounts, profile, insights } = state.data;
  // При включённом входе имени пользователя в API нет — здороваемся без имени
  const greeting = auth.user.auth_enabled ? 'Добрый день' : `Добрый день, ${user.name.split(' ')[0]}`;

  return (
    <div className={styles.page}>
      <header className={styles.greeting}>
        <p className={styles.date}>{formatToday()}</p>
        <h1 className={styles.title}>{greeting}</h1>
      </header>

      {/* Сначала привычное интернет-банку — счета и действия, затем аналитика по компании */}
      <div className={styles.grid}>
        <AccountsWidget accounts={accounts} />
        <QuickActions />
      </div>

      <InsightsEntryCard
        company={company}
        profile={profile}
        topInsights={topByImportance(insights.items, 3)}
        total={insights.total}
      />
    </div>
  );
}
