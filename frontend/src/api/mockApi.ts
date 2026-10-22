import type { Company, CompanyProfile, Insight, InsightsQuery, Page } from '../types';
import companiesJson from '../mocks/companies.json';
import insightsJson from '../mocks/insights.json';
import profilesJson from '../mocks/profiles.json';
import { ApiError } from './errors';

// Имитация API бэкенда на моковых данных. Ведёт себя как backend/app/services/mock_store.py:
// те же фильтры, сортировка и ошибки 404, чтобы переход на настоящий бэкенд ничего не ломал.

const companies = companiesJson as Company[];
const insights = insightsJson as Insight[];
const profiles = profilesJson as CompanyProfile[];

function delay<T>(data: T, ms = 250): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(data), ms));
}

function notFound(detail: string): Promise<never> {
  return Promise.reject(new ApiError(404, 'not_found', detail));
}

export function listCompanies(limit: number, offset: number): Promise<Page<Company>> {
  return delay({ items: companies.slice(offset, offset + limit), total: companies.length, limit, offset });
}

export function getCompany(companyId: string): Promise<Company> {
  const company = companies.find((c) => c.id === companyId);
  return company ? delay(company) : notFound(`Компания ${companyId} не найдена`);
}

export function getCompanyProfile(companyId: string): Promise<CompanyProfile> {
  const company = companies.find((c) => c.id === companyId);
  if (!company) return notFound(`Компания ${companyId} не найдена`);
  const profile = profiles.find((p) => p.company_id === companyId);
  // Как на бэкенде: если профиль не заполняли, отдаём известное из данных компании
  return delay(
    profile ?? {
      company_id: companyId,
      industry: company.industry,
      regions: [company.region],
      key_clients: [],
      key_suppliers: [],
      raw_materials: [],
      currency_risks: [],
      debt_load: '',
      strategic_goals: [],
      bank_products: [],
      manual_data: {},
    },
  );
}

export function listInsights(companyId: string, query: InsightsQuery): Promise<Page<Insight>> {
  if (!companies.some((c) => c.id === companyId)) return notFound(`Компания ${companyId} не найдена`);
  const { type, min_importance, date_from, date_to, limit = 20, offset = 0 } = query;

  const items = insights
    .filter((i) => i.company_id === companyId)
    .filter((i) => type === undefined || i.type === type)
    .filter((i) => min_importance === undefined || i.importance >= min_importance)
    .filter((i) => date_from === undefined || i.created_at.slice(0, 10) >= date_from)
    .filter((i) => date_to === undefined || i.created_at.slice(0, 10) <= date_to)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  return delay({ items: items.slice(offset, offset + limit), total: items.length, limit, offset });
}

export function getInsight(insightId: string): Promise<Insight> {
  const insight = insights.find((i) => i.id === insightId);
  return insight ? delay(insight) : notFound(`Инсайт ${insightId} не найден`);
}
