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
  // Необязательные поля от агента (ветка feat/agent). В моках бэкенда их нет.
  details?: InsightDetails | null;
  used_profile_fields?: string[];
  role?: Role | null;
  generated_by?: 'llm' | 'rules' | null;
  prompt_version?: string | null;
}

/** Роль читателя: меняет отбор и акценты инсайтов. */
export type Role = 'owner' | 'ceo' | 'cfo' | 'treasurer' | 'accountant' | 'procurement';

// ---------- 6 блоков карточки (схема Insight из llm/schemas.py, ветка feat/llm) ----------

export type Level = 'low' | 'medium' | 'high';

/**
 * Утверждение с происхождением: fact — из новости или данных компании,
 * bank — позиция Альфа-Банка, ai — вывод модели.
 * source_ids: N:<id> — новость, A:<док>:<фрагмент> — аналитика банка,
 * F:<КОЛОНКА> — признак компании, P:<id> — контрагент по платежам, G:<id> — группа компаний.
 */
export interface Statement {
  text: string;
  origin: OriginKind;
  source_ids: string[];
}

export interface Consequence extends Statement {
  kind: 'risk' | 'opportunity' | 'factor';
  level: Level;
  horizon: 'immediate' | 'short' | 'medium' | 'long';
}

export interface InsightAction {
  text: string;
  type: 'action' | 'question' | 'scenario';
  bank_product: string | null;
  source_ids: string[];
}

/** Служебная часть от агента: релевантность, слагаемые скоринга, заметки самопроверки. */
export interface AgentMeta {
  run_id: string;
  relevance: number; // 0–1
  score_parts: Record<string, number>;
  macro_sign?: string;
  validation_notes: string[];
  path?: string;
  fallback_reason?: string;
}

export interface InsightDetails {
  news_id: string;
  company_id: string;
  headline: string;
  impact_type: 'risk' | 'opportunity' | 'mixed' | 'neutral';
  importance: Level;
  confidence: Level;
  what_happened: Statement[]; // 01
  why_it_matters: Statement[]; // 02
  consequences: Consequence[]; // 03
  actions: InsightAction[]; // 04
  bank_view: Statement[]; // 05
  company_features_used: string[]; // 06
  data_gaps: string[];
  agent?: AgentMeta;
}

// ---------- Фоновые задачи и обратная связь ----------

export interface JobAccepted {
  job_id: string;
}

export interface Job {
  id: string;
  status: 'pending' | 'running' | 'done' | 'failed';
  error: string | null;
}

export interface TraceStep {
  step: number;
  name: string;
  tool: string | null;
  input: Record<string, unknown>;
  output: unknown;
  status: 'ok' | 'fallback' | 'error' | 'skipped';
  duration_ms: number;
  note: string | null;
}

/** План и трейс прогона агента (GET /jobs/{id}/run) — для панели трассировки. */
export interface AgentRun {
  run_id: string;
  role: Role;
  llm_mode: string;
  plan: string[];
  trace: TraceStep[];
  llm_usage: Record<string, number>;
}

export type FeedbackRating = 'useful' | 'not_useful';

export interface Feedback {
  insight_id: string;
  rating: FeedbackRating;
  comment: string | null;
  created_at: string;
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
  roleKey: Role;
}

/** Счета — часть «обычного» интернет-банка для правдоподобия главной, в API их нет. */
export interface Account {
  id: string;
  title: string;
  currency: 'RUB' | 'CNY' | 'USD' | 'EUR';
  balance: number;
  maskedNumber: string;
}
