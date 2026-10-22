import type { ImpactLevel, InsightType, OriginKind } from '../../types';
import { insightTypeLabel } from '../../utils/insight';
import styles from './Tags.module.css';

const originLabel: Record<OriginKind, string> = {
  fact: 'Факт',
  bank: 'Аналитика банка',
  ai: 'Вывод AI',
};

const originHint: Record<OriginKind, string> = {
  fact: 'Подтверждённая информация из новостного источника',
  bank: 'Позиция экспертов Альфа-Банка',
  ai: 'Сформировано моделью на основе данных о компании',
};

/** Метка происхождения вывода — чтобы пользователь видел, что от банка, а что от AI (критерий 03). */
export function OriginChip({ kind }: { kind: OriginKind }) {
  return (
    <span className={`${styles.chip} ${styles[kind]}`} title={originHint[kind]}>
      {originLabel[kind]}
    </span>
  );
}

const impactLabel: Record<ImpactLevel, string> = {
  high: 'Высокое влияние',
  medium: 'Среднее влияние',
  low: 'Низкое влияние',
};

export function ImpactPill({ level }: { level: ImpactLevel }) {
  return (
    <span className={`${styles.pill} ${styles[`impact-${level}`]}`}>
      <span className={styles.dot} aria-hidden="true" />
      {impactLabel[level]}
    </span>
  );
}

export function TypeTag({ type }: { type: InsightType }) {
  return <span className={`${styles.chip} ${styles[`type-${type}`]}`}>{insightTypeLabel[type]}</span>;
}
