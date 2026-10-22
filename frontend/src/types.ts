// Типы данных. Типы API повторяют контракт бэкенда: docs/api-contract.md и
// backend/app/schemas/*.py в ветке feat/backend. Если контракт меняется — правим здесь.

// ---------- API бэкенда ----------

/** Единая форма всех списков. */
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** Единая форма всех ошибок. */
export interface ApiErrorBody {
  error: 'not_found' | 'validation_error' | 'method_not_allowed' | 'internal_error' | string;
  detail: string;
}

export interface Company {
  id: string;
  name: string;
  industry: string;
  region: string;
  revenue_bln_rub: number;
}

export interface CompanyProfile {
  company_id: string;
  industry: string;
  regions: string[];
  key_clients: string[];
  key_suppliers: string[];
  raw_materials: string[];
  currency_risks: string[];
  debt_load: string;
  strategic_goals: string[];
  bank_products: string[];
  manual_data: Record<string, unknown>;
}

export type InsightType = 'risk' | 'opportunity' | 'event';

/** external — внешняя новость, bank_analytics — аналитика Альфа-Банка. */
export type SourceKind = 'external' | 'bank_analytics';

export interface Source {
  title: string;
  url: string;
  published_at: string; // ISO в UTC с «Z»
  source_kind: SourceKind;
}

export interface Insight {
  id: string;
  company_id: string;
  title: string;
  summary: string;
  type: InsightType;
  importance: number; // 0–100
  impact_on_company: string;
  recommended_action: string;
  sources: Source[];
  created_at: string; // ISO в UTC с «Z»
}

export type InsightsQuery = {
  type?: InsightType;
  min_importance?: number;
  date_from?: string; // YYYY-MM-DD
  date_to?: string; // YYYY-MM-DD
  limit?: number;
  offset?: number;
};

// ---------- Только фронтенд ----------

/** Происхождение вывода для меток в интерфейсе: факт, позиция банка, вывод модели. */
export type OriginKind = 'fact' | 'bank' | 'ai';

export type ImpactLevel = 'high' | 'medium' | 'low';

/** Пользователь интернет-банка. В API бэкенда его пока нет, данные из моков. */
export interface UserProfile {
  name: string;
  role: string;
}

/** Счета — часть «обычного» интернет-банка для правдоподобия главной, в API их нет. */
export interface Account {
  id: string;
  title: string;
  currency: 'RUB' | 'CNY' | 'USD' | 'EUR';
  balance: number;
  maskedNumber: string;
}
