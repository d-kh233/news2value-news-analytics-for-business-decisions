import { createContext, useContext, useState, type ReactNode } from 'react';
import { listCompanies } from '../api/client';
import { useAsync } from '../api/useAsync';
import type { Company } from '../types';

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
  /** id последней задачи агента по компании — для панели трассировки. */
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

export function CurrentCompanyProvider({ children }: { children: ReactNode }) {
  const state = useAsync(() => listCompanies(100, 0), []);
  const [selectedId, setSelectedId] = useState<string | null>(readSavedId);
  const [insightsVersion, setInsightsVersion] = useState(0);
  const [runJobs, setRunJobs] = useState<Record<string, string>>({});

  const companies = state.status === 'success' ? state.data.items : [];
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
