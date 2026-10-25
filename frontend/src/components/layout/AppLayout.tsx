import { Outlet } from 'react-router';
import { listInsights } from '../../api/client';
import { useAsync } from '../../api/useAsync';
import { LoginPage } from '../../pages/LoginPage';
import { AuthProvider, useAuthState } from '../../state/Auth';
import { CurrentCompanyProvider, useCurrentCompany } from '../../state/CurrentCompany';
import { AppHeader } from './AppHeader';
import { Sidebar } from './Sidebar';
import styles from './AppLayout.module.css';

export function AppLayout() {
  const { state, reload } = useAuthState();

  if (state.status === 'loading') {
    return (
      <p className={styles.fullscreen} role="status">
        Загружаем интернет-банк…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <div className={styles.fullscreen} role="alert">
        <p>Сервер недоступен. Проверьте, что бэкенд запущен, и обновите страницу.</p>
        <button type="button" className={styles.retry} onClick={reload}>
          Повторить
        </button>
      </div>
    );
  }
  if (state.status === 'login') {
    return <LoginPage onLoggedIn={reload} />;
  }

  return (
    <AuthProvider user={state.user} onLogout={reload}>
      <CurrentCompanyProvider>
        <Shell />
      </CurrentCompanyProvider>
    </AuthProvider>
  );
}

function Shell() {
  const { company, insightsVersion } = useCurrentCompany();
  const insightsCount = useAsync(
    () => (company ? listInsights(company.id, { limit: 1 }).then((page) => page.total) : Promise.resolve(0)),
    [company?.id, insightsVersion],
  );

  return (
    <div className={styles.shell}>
      {/* Ссылка «перейти к содержимому» для клавиатуры и скринридеров */}
      <a className={styles.skipLink} href="#main">
        Перейти к основному содержимому
      </a>
      <AppHeader />
      <div className={styles.body}>
        <Sidebar insightsCount={insightsCount.status === 'success' ? insightsCount.data : 0} />
        <main id="main" className={styles.main} tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
