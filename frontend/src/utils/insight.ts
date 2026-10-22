import type { ImpactLevel, Insight, InsightType, OriginKind } from '../types';

// Перевод полей инсайта из API в то, что видит пользователь.

/** Важность 0–100 → уровень влияния. Пороги согласованы с llm/backend_adapter.py (85 / 60 / 35). */
export function impactLevel(importance: number): ImpactLevel {
  if (importance >= 80) return 'high';
  if (importance >= 55) return 'medium';
  return 'low';
}

export const insightTypeLabel: Record<InsightType, string> = {
  risk: 'Риск',
  opportunity: 'Возможность',
  event: 'Событие',
};

/**
 * Метки происхождения для карточки: внешняя новость → «Факт», аналитика банка → «Аналитика банка».
 * «Вывод AI» есть всегда: объяснение влияния на компанию формирует модель.
 */
export function insightOrigins(insight: Insight): OriginKind[] {
  const origins: OriginKind[] = [];
  if (insight.sources.some((s) => s.source_kind === 'external')) origins.push('fact');
  if (insight.sources.some((s) => s.source_kind === 'bank_analytics')) origins.push('bank');
  origins.push('ai');
  return origins;
}

/** Самые важные инсайты — для превью на главной. */
export function topByImportance(insights: Insight[], count: number): Insight[] {
  return [...insights].sort((a, b) => b.importance - a.importance).slice(0, count);
}
