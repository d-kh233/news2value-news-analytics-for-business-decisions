import type {
  AgentRun,
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
} from '../types';
import agentInsightsJson from '../mocks/agent-insights.json';
import agentRunsJson from '../mocks/agent-runs.json';
import companiesJson from '../mocks/companies.json';
import insightsJson from '../mocks/insights.json';
import profilesJson from '../mocks/profiles.json';
import { asSentence } from '../utils/labels';
import { ApiError } from './errors';

// Имитация API бэкенда на моковых данных. Ведёт себя как режим USE_MOCKS=true на бэкенде
// (docs/api-contract.md): те же фильтры, ошибки и фоновые задачи (refresh, ask).
//
// agent-insights.json и agent-runs.json — настоящий вывод агента из feat/agent,
// прогнанного на этих же компаниях в режиме LLM_MODE=stub (без модели, по правилам).
// Как и на бэкенде в режиме моков, данные живут в памяти и сбрасываются при перезагрузке страницы.

const companies = companiesJson as Company[];
const profiles = profilesJson as CompanyProfile[];
const agentInsights = agentInsightsJson as unknown as Insight[];
const agentRuns = agentRunsJson as unknown as Record<string, AgentRun>;

let insights: Insight[] = insightsJson as Insight[];

const AGENT_RUN_MS = 2500; // сколько «думает» агент в моках
const ASK_RUN_MS = 1500; // сколько готовится ответ на вопрос
const DEFAULT_ROLE: Role = 'ceo';
const QUESTIONS_PER_INSIGHT = 20;

type MockJob =
  | { job: Job; kind: 'refresh'; companyId: string; role: Role; startedAt: number }
  | { job: Job; kind: 'ask'; questionId: string; startedAt: number };

const jobs = new Map<string, MockJob>();
const feedback = new Map<string, Feedback>();
const questions: Question[] = [];

function delay<T>(data: T, ms = 250): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(data), ms));
}

function notFound(detail: string): Promise<never> {
  return Promise.reject(new ApiError(404, 'not_found', detail));
}

function newId(prefix: string): string {
  return `${prefix}-${Math.random().toString(16).slice(2, 10)}`;
}

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d+Z$/, 'Z');
}

function companyExists(companyId: string): boolean {
  return companies.some((c) => c.id === companyId);
}

// ---------- Вход: в моках вход выключен, как AUTH_ENABLED=false на сервере ----------

const demoUser: UserInfo = {
  id: 'demo',
  email: null,
  role: 'admin',
  company_id: null,
  is_admin: true,
  auth_enabled: false,
};

export function getMe(): Promise<UserInfo> {
  return delay(demoUser, 50);
}

export function login(email: string): Promise<LoginResponse> {
  return delay({ access_token: 'mock-token', token_type: 'bearer', expires_in: 43200, user: { ...demoUser, email } });
}

// ---------- Компании ----------

export function listCompanies(limit: number, offset: number, q?: string): Promise<Page<Company>> {
  const needle = q?.trim().toLowerCase();
  const found = needle
    ? companies.filter((c) => [c.id, c.name, c.industry].join(' ').toLowerCase().includes(needle))
    : companies;
  return delay({ items: found.slice(offset, offset + limit), total: found.length, limit, offset });
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

function runningRefresh(companyId: string): string | null {
  for (const [id, entry] of jobs) {
    if (entry.kind === 'refresh' && entry.companyId === companyId && entry.job.status !== 'done') return id;
  }
  return null;
}

export function listInsights(companyId: string, query: InsightsQuery): Promise<InsightPage> {
  if (!companyExists(companyId)) return notFound(`Компания ${companyId} не найдена`);
  const { type, min_importance, date_from, date_to, limit = 20, offset = 0 } = query;

  const own = insights.filter((i) => i.company_id === companyId);
  const items = own
    .filter((i) => type === undefined || i.type === type)
    .filter((i) => min_importance === undefined || i.importance >= min_importance)
    .filter((i) => date_from === undefined || i.created_at.slice(0, 10) >= date_from)
    .filter((i) => date_to === undefined || i.created_at.slice(0, 10) <= date_to)
    .sort((a, b) => b.importance - a.importance || b.created_at.localeCompare(a.created_at));

  const refreshJobId = runningRefresh(companyId);
  const hint =
    own.length > 0 ? null : refreshJobId ? 'Инсайты собираются…' : 'Инсайты ещё не собраны, нажмите «Обновить»';

  return delay({
    items: items.slice(offset, offset + limit),
    total: items.length,
    limit,
    offset,
    hint,
    refresh_job_id: refreshJobId,
  });
}

export function getInsight(insightId: string): Promise<Insight> {
  const insight = insights.find((i) => i.id === insightId);
  return insight ? delay(insight) : notFound(`Инсайт ${insightId} не найден`);
}

export function getInsightTrace(insightId: string): Promise<InsightTrace> {
  const insight = insights.find((i) => i.id === insightId);
  if (!insight) return notFound(`Инсайт ${insightId} не найден`);
  const runId = insight.details?.agent?.run_id;
  const run = runId ? Object.values(agentRuns).find((r) => r.run_id === runId) : undefined;
  const totalMs = run?.trace.reduce((sum, step) => sum + (step.duration_ms ?? 0), 0) ?? null;
  return delay({
    insight_id: insight.id,
    company_id: insight.company_id,
    run_id: run ? run.run_id : null,
    generator_run_id: run ? run.run_id : null,
    run_status: run ? 'done' : null,
    role: insight.role ?? null,
    llm_mode: run?.llm_mode ?? null,
    started_at: run ? insight.created_at : null,
    finished_at: run ? insight.created_at : null,
    duration_ms: totalMs,
    steps: run?.trace ?? [],
    sources: insight.sources,
    used_profile_fields: insight.used_profile_fields ?? null,
    prompt_version: insight.prompt_version ?? null,
    generated_by: insight.generated_by ?? null,
  });
}

export function sendFeedback(
  insightId: string,
  rating: FeedbackRating,
  reason?: FeedbackReason,
  comment?: string,
): Promise<Feedback> {
  if (!insights.some((i) => i.id === insightId)) return notFound(`Инсайт ${insightId} не найден`);
  const previous = feedback.get(insightId);
  const saved: Feedback = {
    insight_id: insightId,
    rating,
    reason: rating === 'not_useful' ? (reason ?? null) : null,
    comment: comment ?? null,
    created_at: previous?.created_at ?? nowIso(),
    updated_at: nowIso(),
  };
  feedback.set(insightId, saved);
  return delay(saved);
}

export function getMyFeedback(insightId: string): Promise<Feedback | null> {
  if (!insights.some((i) => i.id === insightId)) return notFound(`Инсайт ${insightId} не найден`);
  return delay(feedback.get(insightId) ?? null, 100);
}

// ---------- Вопросы по инсайту ----------

/** Ответ-шаблон по полям инсайта — так отвечает бэкенд, когда модель недоступна. */
function templateAnswer(insight: Insight, question: string): string {
  const why = insight.details?.why_it_matters.map((s) => asSentence(s.text)).join(' ') || insight.impact_on_company;
  const action = asSentence(insight.details?.actions[0]?.text || insight.recommended_action);
  const bank = insight.details?.bank_view.map((s) => asSentence(s.text)).join(' ');
  return [
    `Вопрос: «${question}».`,
    `Почему событие важно для компании: ${why}`,
    action ? `Что можно сделать: ${action}` : '',
    bank ? `Позиция аналитиков банка: ${bank}` : '',
    'Это демонстрационный ответ по полям карточки, без языковой модели.',
  ]
    .filter(Boolean)
    .join('\n\n');
}

export function askQuestion(insightId: string, question: string): Promise<AskAccepted> {
  const insight = insights.find((i) => i.id === insightId);
  if (!insight) return notFound(`Инсайт ${insightId} не найден`);
  const text = question.trim();
  if (!text) return Promise.reject(new ApiError(422, 'validation_error', 'вопрос пустой'));
  if (questions.filter((q) => q.insight_id === insightId).length >= QUESTIONS_PER_INSIGHT) {
    return Promise.reject(new ApiError(409, 'conflict', `Лимит ${QUESTIONS_PER_INSIGHT} вопросов к инсайту исчерпан`));
  }
  const jobId = newId('job');
  const questionId = newId('q');
  questions.push({
    id: questionId,
    insight_id: insightId,
    question: text,
    answer: null,
    sources: [],
    status: 'pending',
    job_id: jobId,
    created_at: nowIso(),
  });
  jobs.set(jobId, { job: { id: jobId, status: 'pending', error: null }, kind: 'ask', questionId, startedAt: Date.now() });
  return delay({ job_id: jobId, question_id: questionId });
}

export function listQuestions(insightId: string): Promise<Page<Question>> {
  if (!insights.some((i) => i.id === insightId)) return notFound(`Инсайт ${insightId} не найден`);
  const items = questions.filter((q) => q.insight_id === insightId);
  return delay({ items: items.map((q) => ({ ...q })), total: items.length, limit: 100, offset: 0 }, 100);
}

// ---------- Агент: обновление инсайтов ----------

export function refreshInsights(companyId: string, role?: Role): Promise<JobAccepted> {
  if (!companyExists(companyId)) return notFound(`Компания ${companyId} не найдена`);
  // Как на бэкенде: одна задача на компанию — повторное нажатие возвращает текущую
  const running = runningRefresh(companyId);
  if (running) return delay({ job_id: running });
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

function finishJob(entry: MockJob): void {
  if (entry.kind === 'refresh') {
    // Новые инсайты компании заменяют все прежние
    const fresh = agentResultFor(entry.companyId, entry.role).map((i) => ({ ...i, created_at: nowIso() }));
    insights = [...insights.filter((i) => i.company_id !== entry.companyId), ...fresh];
    return;
  }
  const question = questions.find((q) => q.id === entry.questionId);
  const insight = question && insights.find((i) => i.id === question.insight_id);
  if (!question) return;
  if (!insight) {
    question.status = 'failed';
    entry.job = { ...entry.job, status: 'failed', error: 'Инсайт заменён обновлением ленты' };
    return;
  }
  question.answer = templateAnswer(insight, question.question);
  question.sources = insight.sources;
  question.status = 'done';
}

export function getJob(jobId: string): Promise<Job> {
  const entry = jobs.get(jobId);
  if (!entry) return notFound(`Задача ${jobId} не найдена`);

  if (entry.job.status === 'pending' || entry.job.status === 'running') {
    const elapsed = Date.now() - entry.startedAt;
    const total = entry.kind === 'refresh' ? AGENT_RUN_MS : ASK_RUN_MS;
    if (elapsed >= total) {
      entry.job = { ...entry.job, status: 'done' };
      finishJob(entry);
    } else if (elapsed > 300) {
      entry.job = { ...entry.job, status: 'running' };
      if (entry.kind === 'ask') {
        const question = questions.find((q) => q.id === entry.questionId);
        if (question) question.status = 'running';
      }
    }
  }
  return delay(entry.job, 100);
}

export function getJobRun(jobId: string): Promise<AgentRun> {
  const entry = jobs.get(jobId);
  if (!entry || entry.kind !== 'refresh' || entry.job.status !== 'done') {
    return notFound(`Прогон задачи ${jobId} не найден или ещё не завершён`);
  }
  const run = agentRuns[`${entry.companyId}:${entry.role}`] ?? agentRuns[`${entry.companyId}:${DEFAULT_ROLE}`];
  return run ? delay(run) : notFound(`Прогон задачи ${jobId} не найден`);
}
