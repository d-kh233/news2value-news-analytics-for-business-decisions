import type {
  Account,
  AgentRun,
  ApiErrorBody,
  AskAccepted,
  Company,
  CompanyProfile,
  Feedback,
  FeedbackRating,
  FeedbackReason,
  Insight,
  InsightPage,
  InsightsQuery,
  InsightTrace,
  Job,
  JobAccepted,
  LoginResponse,
  Page,
  Question,
  Role,
  UserInfo,
  UserProfile,
} from '../types';
import { mockAccounts, mockUser } from '../mocks/home';
import { ApiError } from './errors';
import * as mock from './mockApi';
import { getToken, notifyUnauthorized } from './token';

// Единая точка доступа к бэкенду. Пути и формы ответов — по docs/api-contract.md
// (ветка feat/llm-fixes-daria, модули B7–B9).
// VITE_USE_MOCKS=true (по умолчанию) — данные из src/mocks, бэкенд не нужен.
// VITE_USE_MOCKS=false — запросы идут на /api, а dev-сервер Vite (или nginx на сервере)
// пересылает их на бэкенд без префикса /api, поэтому CORS не нужен.
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS !== 'false';
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

/** Демо-компании с готовыми инсайтами (после scripts/seed_demo.py на сервере). */
export const DEMO_COMPANY_IDS: string[] = (import.meta.env.VITE_DEMO_COMPANY_IDS ?? 'U_67064,U_19717,U_16346')
  .split(',')
  .map((id: string) => id.trim())
  .filter(Boolean);

function toQueryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

async function call<T>(path: string, init: RequestInit = {}, options: { noAuthRedirect?: boolean } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body !== undefined) headers.set('Content-Type', 'application/json');

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    if (response.status === 401 && !options.noAuthRedirect) notifyUnauthorized();
    throw new ApiError(response.status, body?.error ?? 'unknown', body?.detail ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function request<T>(path: string): Promise<T> {
  return call<T>(path);
}

function send<T>(path: string, method: 'POST' | 'PUT', body?: unknown): Promise<T> {
  return call<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}

// ---------- Вход ----------

/** Кто вошёл. При выключенном входе на сервере — demo-администратор с auth_enabled=false. */
export function getMe(): Promise<UserInfo> {
  if (USE_MOCKS) return mock.getMe();
  return call('/auth/me', {}, { noAuthRedirect: true });
}

export function login(email: string, password: string): Promise<LoginResponse> {
  if (USE_MOCKS) return mock.login(email);
  return call('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }, { noAuthRedirect: true });
}

// ---------- Компании ----------

export function listCompanies(limit = 20, offset = 0, q?: string): Promise<Page<Company>> {
  if (USE_MOCKS) return mock.listCompanies(limit, offset, q);
  return request(`/companies${toQueryString({ limit, offset, q })}`);
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

export function listInsights(companyId: string, query: InsightsQuery = {}): Promise<InsightPage> {
  if (USE_MOCKS) return mock.listInsights(companyId, query);
  return request(`/companies/${encodeURIComponent(companyId)}/insights${toQueryString(query)}`);
}

export function getInsight(insightId: string): Promise<Insight> {
  if (USE_MOCKS) return mock.getInsight(insightId);
  return request(`/insights/${encodeURIComponent(insightId)}`);
}

/** Как сделан инсайт: запуск агента, шаги, источники, признаки профиля. */
export function getInsightTrace(insightId: string): Promise<InsightTrace> {
  if (USE_MOCKS) return mock.getInsightTrace(insightId);
  return request(`/insights/${encodeURIComponent(insightId)}/trace`);
}

export function sendFeedback(
  insightId: string,
  rating: FeedbackRating,
  reason?: FeedbackReason,
  comment?: string,
): Promise<Feedback> {
  // reason передаётся только при not_useful, иначе сервер ответит 422
  const body = { rating, reason: rating === 'not_useful' ? reason : undefined, comment };
  if (USE_MOCKS) return mock.sendFeedback(insightId, body.rating, body.reason, comment);
  return send(`/insights/${encodeURIComponent(insightId)}/feedback`, 'POST', body);
}

/** Моя оценка инсайта или null, если ещё не оценивала. */
export function getMyFeedback(insightId: string): Promise<Feedback | null> {
  if (USE_MOCKS) return mock.getMyFeedback(insightId);
  return request(`/insights/${encodeURIComponent(insightId)}/feedback`);
}

// ---------- Вопросы по инсайту ----------

export function askQuestion(insightId: string, question: string): Promise<AskAccepted> {
  if (USE_MOCKS) return mock.askQuestion(insightId, question);
  return send(`/insights/${encodeURIComponent(insightId)}/ask`, 'POST', { question });
}

/** История вопросов текущего пользователя по инсайту, старые первыми. */
export function listQuestions(insightId: string): Promise<Page<Question>> {
  if (USE_MOCKS) return mock.listQuestions(insightId);
  return request(`/insights/${encodeURIComponent(insightId)}/questions${toQueryString({ limit: 100 })}`);
}

// ---------- Агент и фоновые задачи ----------

/** Запустить агента: когда задача done, новые инсайты компании заменяют прежние. */
export function refreshInsights(companyId: string, role?: Role): Promise<JobAccepted> {
  if (USE_MOCKS) return mock.refreshInsights(companyId, role);
  return send(`/companies/${encodeURIComponent(companyId)}/insights/refresh${toQueryString({ role })}`, 'POST');
}

export function getJob(jobId: string): Promise<Job> {
  if (USE_MOCKS) return mock.getJob(jobId);
  return request(`/jobs/${encodeURIComponent(jobId)}`);
}

/** План и трейс прогона агента. */
export function getJobRun(jobId: string): Promise<AgentRun> {
  if (USE_MOCKS) return mock.getJobRun(jobId);
  return request(`/jobs/${encodeURIComponent(jobId)}/run`);
}

/** Опрашивает статус задачи, пока она не завершится. */
export async function waitForJob(jobId: string, intervalMs = 1500, timeoutMs = 10 * 60 * 1000): Promise<Job> {
  const started = Date.now();
  for (;;) {
    const job = await getJob(jobId);
    if (job.status === 'done' || job.status === 'failed') return job;
    if (Date.now() - started > timeoutMs) throw new Error('Агент не ответил за отведённое время');
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

// ---------- Только фронтенд: в API бэкенда этого нет ----------

/** Имя и должность для шапки — только для демо-вида интернет-банка, в API их нет. */
export function getCurrentUser(): Promise<UserProfile> {
  return Promise.resolve(mockUser);
}

export function getAccounts(): Promise<Account[]> {
  return Promise.resolve(mockAccounts);
}
