import { createBrowserRouter } from 'react-router';
import { AppLayout } from './components/layout/AppLayout';
import { HomePage } from './pages/HomePage';
import { AnalyticsPage } from './pages/AnalyticsPage';
import { InsightPage } from './pages/InsightPage';
import { StubPage } from './pages/StubPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'analytics', element: <AnalyticsPage /> },
      { path: 'analytics/:insightId', element: <InsightPage /> },
      { path: 'accounts', element: <StubPage title="Счета" /> },
      { path: 'payments', element: <StubPage title="Платежи" /> },
      { path: 'credits', element: <StubPage title="Кредиты и гарантии" /> },
      { path: 'ved', element: <StubPage title="ВЭД" /> },
      { path: '*', element: <StubPage title="Страница не найдена" /> },
    ],
  },
]);
