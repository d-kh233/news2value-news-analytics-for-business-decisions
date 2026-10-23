import type {
  AgentRun,
  Company,
  CompanyProfile,
  Feedback,
  FeedbackRating,
  Insight,
  InsightsQuery,
  Job,
  JobAccepted,
  Page,
  Role,
} from '../types';
import agentInsightsJson from '../mocks/agent-insights.json';
import agentRunsJson from '../mocks/agent-runs.json';
import companiesJson from '../mocks/companies.json';
import insightsJson from '../mocks/insights.json';
import profilesJson from '../mocks/profiles.json';
import { ApiError } from './errors';

// Имитация API бэкенда на моковых данных. Ведёт себя как backend/app/services/mock_store.py
// (ветка feat/agent): те же фильтры, сортировка, ошибки 404 и фоновые задачи.
//
// agent-insights.json и agent-runs.json — настоящий вывод агента из feat/agent,
// прогнанного на этих же компаниях в режиме LLM_MODE=stub (без модели, по правилам).
// Как и на бэкенде, данные живут в памяти и сбрасываются при перезагрузке страницы.

const companies = companiesJson as Company[];
const profiles = profilesJson as CompanyProfile[];
const agentInsights = agentInsightsJson as unknown as Insight[];
const agentRuns = agentRunsJson as unknown as Record<string, AgentRun>;

let insights: Insight[] = insightsJson as Insight[];

const AGENT_RUN_MS = 2500; // сколько «думает» агент в моках
const DEFAULT_ROLE: Role = 'ceo';

interface MockJob {
  job: Job;
  kind: 'refresh';
  companyId: string;
  role: Role;
  startedAt: number;
}

const jobs = new Map<string, MockJob>();
const feedback = new Map<string, Feedback>();

function delay<T>(data: T, ms = 250): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(data), ms));
}

function notFound(detail: string): Promise<never> {
  return Promise.reject(new ApiError(404, 'not_found', detail));
}

function newId(prefix: string): string {
  return `${prefix}-${Math.random().toString(16).slice(2, 10)}`;
}

function companyExists(companyId: string): boolean {
  return companies.some((c) => c.id === companyId);
}

// ---------- Компании ----------

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

// ---------- Инсайты ----------

export function listInsights(companyId: string, query: InsightsQuery): Promise<Page<Insight>> {
  if (!companyExists(companyId)) return notFound(`Компания ${companyId} не найдена`);
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

export function sendFeedback(insightId: string, rating: FeedbackRating, comment?: string): Promise<Feedback> {
  if (!insights.some((i) => i.id === insightId)) return notFound(`Инсайт ${insightId} не найден`);
  const saved: Feedback = {
    insight_id: insightId,
    rating,
    comment: comment ?? null,
    created_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
  };
  feedback.set(insightId, saved);
  return delay(saved);
}

// ---------- Агент: обновление инсайтов ----------

export function refreshInsights(companyId: string, role?: Role): Promise<JobAccepted> {
  if (!companyExists(companyId)) return notFound(`Компания ${companyId} не найдена`);
  const id = newId('job');
  jobs.set(id, {
    job: { id, status: 'pending', error: null },
    kind: 'refresh',
    companyId,
    role: role ?? DEFAULT_ROLE,
    startedAt: Date.now(),
  });
  return delay({ job_id: id });
}

function agentResultFor(companyId: string, role: Role): Insight[] {
  const forRole = agentInsights.filter((i) => i.company_id === companyId && i.role === role);
  // Записанного прогона для этой роли нет — берём роль по умолчанию
  return forRole.length > 0
    ? forRole
    : agentInsights.filter((i) => i.company_id === companyId && i.role === DEFAULT_ROLE);
}

export function getJob(jobId: string): Promise<Job> {
  const entry = jobs.get(jobId);
  if (!entry) return notFound(`Задача ${jobId} не найдена`);

  if (entry.job.status !== 'done') {
    const elapsed = Date.now() - entry.startedAt;
    if (elapsed >= AGENT_RUN_MS) {
      // Как на бэкенде: новые инсайты компании заменяют все прежние
      const fresh = agentResultFor(entry.companyId, entry.role).map((i) => ({
        ...i,
        created_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
      }));
      insights = [...insights.filter((i) => i.company_id !== entry.companyId), ...fresh];
      entry.job = { ...entry.job, status: 'done' };
    } else if (elapsed > 300) {
      entry.job = { ...entry.job, status: 'running' };
    }
  }
  return delay(entry.job, 100);
}

export function getJobRun(jobId: string): Promise<AgentRun> {
  const entry = jobs.get(jobId);
  if (!entry || entry.job.status !== 'done') {
    return notFound(`Прогон задачи ${jobId} не найден или ещё не завершён`);
  }
  const run = agentRuns[`${entry.companyId}:${entry.role}`] ?? agentRuns[`${entry.companyId}:${DEFAULT_ROLE}`];
  return run ? delay(run) : notFound(`Прогон задачи ${jobId} не найден`);
}
