import type {
  Account,
  ApiErrorBody,
  Company,
  CompanyProfile,
  Insight,
  InsightsQuery,
  Page,
  UserProfile,
} from '../types';
import { mockAccounts, mockUser } from '../mocks/home';
import { ApiError } from './errors';
import * as mock from './mockApi';

// Единая точка доступа к бэкенду. Пути и формы ответов — по docs/api-contract.md (ветка feat/backend).
// VITE_USE_MOCKS=true (по умолчанию) — данные из src/mocks, бэкенд не нужен.
// VITE_USE_MOCKS=false — запросы идут на /api, а dev-сервер Vite проксирует их на бэкенд
// (см. vite.config.ts), поэтому настройка CORS на бэкенде не требуется.
const USE_MOCKS = import.meta.env.VITE_USE_MOCKS !== 'false';
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

function toQueryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiError(response.status, body?.error ?? 'unknown', body?.detail ?? response.statusText);
  }
  return (await response.json()) as T;
}

// ---------- Компании ----------

export function listCompanies(limit = 20, offset = 0): Promise<Page<Company>> {
  if (USE_MOCKS) return mock.listCompanies(limit, offset);
  return request(`/companies${toQueryString({ limit, offset })}`);
}

export function getCompany(companyId: string): Promise<Company> {
  if (USE_MOCKS) return mock.getCompany(companyId);
  return request(`/companies/${encodeURIComponent(companyId)}`);
}

export function getCompanyProfile(companyId: string): Promise<CompanyProfile> {
  if (USE_MOCKS) return mock.getCompanyProfile(companyId);
  return request(`/companies/${encodeURIComponent(companyId)}/profile`);
}

// ---------- Инсайты ----------

export function listInsights(companyId: string, query: InsightsQuery = {}): Promise<Page<Insight>> {
  if (USE_MOCKS) return mock.listInsights(companyId, query);
  return request(`/companies/${encodeURIComponent(companyId)}/insights${toQueryString(query)}`);
}

export function getInsight(insightId: string): Promise<Insight> {
  if (USE_MOCKS) return mock.getInsight(insightId);
  return request(`/insights/${encodeURIComponent(insightId)}`);
}

// ---------- Только фронтенд: в API бэкенда этого нет ----------

export function getCurrentUser(): Promise<UserProfile> {
  return Promise.resolve(mockUser);
}

export function getAccounts(): Promise<Account[]> {
  return Promise.resolve(mockAccounts);
}
