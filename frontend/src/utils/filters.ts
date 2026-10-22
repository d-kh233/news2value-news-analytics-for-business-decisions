import type { ImpactLevel, Insight, InsightType, SourceKind } from '../types';
import { impactLevel } from './insight';

// Фильтры ленты. Инсайтов у компании немного (до 100), поэтому фильтруем на клиенте:
// ответ мгновенный, и один запрос к API на всю ленту.

export type Period = 'all' | '7' | '30';
export type SortOrder = 'importance' | 'date';

export interface FeedFilters {
  query: string;
  impact: ImpactLevel[];
  types: InsightType[];
  sources: SourceKind[];
  period: Period;
  sort: SortOrder;
}

export const defaultFilters: FeedFilters = {
  query: '',
  impact: ['high', 'medium', 'low'],
  types: ['risk', 'opportunity', 'event'],
  sources: ['external', 'bank_analytics'],
  period: 'all',
  sort: 'importance',
};

const DAY_MS = 24 * 60 * 60 * 1000;

function matchesQuery(insight: Insight, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [insight.title, insight.summary, insight.impact_on_company, insight.recommended_action]
    .join(' ')
    .toLowerCase()
    .includes(q);
}

export function applyFilters(insights: Insight[], filters: FeedFilters, now = Date.now()): Insight[] {
  const minTime = filters.period === 'all' ? -Infinity : now - Number(filters.period) * DAY_MS;

  const result = insights.filter(
    (i) =>
      matchesQuery(i, filters.query) &&
      filters.impact.includes(impactLevel(i.importance)) &&
      filters.types.includes(i.type) &&
      i.sources.some((s) => filters.sources.includes(s.source_kind)) &&
      new Date(i.created_at).getTime() >= minTime,
  );

  return result.sort((a, b) =>
    filters.sort === 'importance'
      ? b.importance - a.importance || b.created_at.localeCompare(a.created_at)
      : b.created_at.localeCompare(a.created_at),
  );
}

/** Сколько фильтров отличается от значений по умолчанию — для счётчика на кнопке «Сбросить». */
export function countActiveFilters(filters: FeedFilters): number {
  let count = 0;
  if (filters.query.trim()) count++;
  if (filters.impact.length !== defaultFilters.impact.length) count++;
  if (filters.types.length !== defaultFilters.types.length) count++;
  if (filters.sources.length !== defaultFilters.sources.length) count++;
  if (filters.period !== defaultFilters.period) count++;
  return count;
}
