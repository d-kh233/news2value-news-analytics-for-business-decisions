import { Outlet } from 'react-router';
import { getCurrentUser, listInsights } from '../../api/client';
import { useAsync } from '../../api/useAsync';
import { CurrentCompanyProvider, useCurrentCompany } from '../../state/CurrentCompany';
import { AppHeader } from './AppHeader';
import { Sidebar } from './Sidebar';
import styles from './AppLayout.module.css';

export function AppLayout() {
  return (
    <CurrentCompanyProvider>
      <Shell />
    </CurrentCompanyProvider>
  );
}

function Shell() {
  const { company } = useCurrentCompany();
  const user = useAsync(getCurrentUser, []);
  const insightsCount = useAsync(
    () => (company ? listInsights(company.id, { limit: 1 }).then((page) => page.total) : Promise.resolve(0)),
    [company?.id],
  );

  return (
    <div className={styles.shell}>
      {/* Ссылка «перейти к содержимому» для клавиатуры и скринридеров */}
      <a className={styles.skipLink} href="#main">
        Перейти к основному содержимому
      </a>
      <AppHeader user={user.status === 'success' ? user.data : undefined} />
      <div className={styles.body}>
        <Sidebar insightsCount={insightsCount.status === 'success' ? insightsCount.data : 0} />
        <main id="main" className={styles.main} tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
