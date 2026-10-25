import { createContext, useContext, useState, type ReactNode } from 'react';
import { DEMO_COMPANY_IDS, USE_MOCKS, getCompany, listCompanies } from '../api/client';
import { useAsync } from '../api/useAsync';
import type { Company, UserInfo } from '../types';
import { useAuth } from './Auth';

// Выбранная компания-клиент. Нужна на всех экранах: главная, лента, карточка события.

interface CurrentCompanyValue {
  companies: Company[];
  company: Company | null;
  selectCompany: (companyId: string) => void;
  loading: boolean;
  error: Error | null;
  /** Растёт после каждого обновления инсайтов агентом — экраны перезагружают данные. */
  insightsVersion: number;
  notifyInsightsChanged: () => void;
  /** id последней задачи агента по компании. */
  lastRunJobId: string | null;
  setLastRunJobId: (companyId: string, jobId: string) => void;
}

const CurrentCompanyContext = createContext<CurrentCompanyValue | null>(null);

const STORAGE_KEY = 'news2value.companyId';

function readSavedId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/**
 * Какие компании показывать в переключателе.
 * Обычный пользователь видит только свою компанию (сервер сам ограничивает GET /companies).
 * Администратору (и при выключенном входе) показываем демо-компании с готовыми инсайтами:
 * в данных банка тысячи компаний без названий, листать их в демо незачем.
 */
async function loadCompanies(user: UserInfo): Promise<Company[]> {
  if (USE_MOCKS || !user.is_admin) {
    return (await listCompanies(100, 0)).items;
  }
  const demo = await Promise.all(DEMO_COMPANY_IDS.map((id) => getCompany(id).catch(() => null)));
  const found = demo.filter((c): c is Company => c !== null);
  return found.length > 0 ? found : (await listCompanies(20, 0)).items;
}

export function CurrentCompanyProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const state = useAsync(() => loadCompanies(user), [user.id]);
  const [selectedId, setSelectedId] = useState<string | null>(readSavedId);
  const [insightsVersion, setInsightsVersion] = useState(0);
  const [runJobs, setRunJobs] = useState<Record<string, string>>({});

  const companies = state.status === 'success' ? state.data : [];
  const company = companies.find((c) => c.id === selectedId) ?? companies[0] ?? null;

  function selectCompany(companyId: string) {
    setSelectedId(companyId);
    try {
      localStorage.setItem(STORAGE_KEY, companyId);
    } catch {
      // без сохранения выбор просто сбросится после перезагрузки
    }
  }

  return (
    <CurrentCompanyContext.Provider
      value={{
        companies,
        company,
        selectCompany,
        loading: state.status === 'loading',
        error: state.status === 'error' ? state.error : null,
        insightsVersion,
        notifyInsightsChanged: () => setInsightsVersion((v) => v + 1),
        lastRunJobId: company ? (runJobs[company.id] ?? null) : null,
        setLastRunJobId: (companyId, jobId) => setRunJobs((prev) => ({ ...prev, [companyId]: jobId })),
      }}
    >
      {children}
    </CurrentCompanyContext.Provider>
  );
}

export function useCurrentCompany(): CurrentCompanyValue {
  const value = useContext(CurrentCompanyContext);
  if (!value) throw new Error('useCurrentCompany нужно вызывать внутри CurrentCompanyProvider');
  return value;
}
