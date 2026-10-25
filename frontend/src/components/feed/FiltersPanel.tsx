import { useState } from 'react';
import type { ImpactLevel, InsightType, SourceKind } from '../../types';
import { countActiveFilters, defaultFilters, type FeedFilters, type Period } from '../../utils/filters';
import styles from './FiltersPanel.module.css';

interface Props {
  filters: FeedFilters;
  onChange: (filters: FeedFilters) => void;
}

const impactOptions: { value: ImpactLevel; label: string }[] = [
  { value: 'high', label: 'Высокое' },
  { value: 'medium', label: 'Среднее' },
  { value: 'low', label: 'Низкое' },
];

const typeOptions: { value: InsightType; label: string }[] = [
  { value: 'risk', label: 'Риски' },
  { value: 'opportunity', label: 'Возможности' },
  { value: 'event', label: 'События' },
];

const sourceOptions: { value: SourceKind; label: string }[] = [
  { value: 'external', label: 'Новости (факты)' },
  { value: 'bank_analytics', label: 'Аналитика Альфа-Банка' },
];

const periodOptions: { value: Period; label: string }[] = [
  { value: 'all', label: 'За всё время' },
  { value: '7', label: 'За 7 дней' },
  { value: '30', label: 'За 30 дней' },
];

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

interface CheckboxGroupProps<T extends string> {
  legend: string;
  options: { value: T; label: string }[];
  selected: T[];
  onChange: (selected: T[]) => void;
}

function CheckboxGroup<T extends string>({ legend, options, selected, onChange }: CheckboxGroupProps<T>) {
  return (
    <fieldset className={styles.group}>
      <legend className={styles.legend}>{legend}</legend>
      {options.map((option) => (
        <label key={option.value} className={styles.check}>
          <input
            type="checkbox"
            checked={selected.includes(option.value)}
            onChange={() => onChange(toggle(selected, option.value))}
          />
          <span>{option.label}</span>
        </label>
      ))}
    </fieldset>
  );
}

export function FiltersPanel({ filters, onChange }: Props) {
  const active = countActiveFilters(filters);
  const [open, setOpen] = useState(() => !window.matchMedia('(max-width: 1000px)').matches);
  const set = <K extends keyof FeedFilters>(key: K, value: FeedFilters[K]) => onChange({ ...filters, [key]: value });

  const content = (
    <>
      <div className={styles.group}>
        <label className={styles.legend} htmlFor="feed-search">
          Поиск
        </label>
        <input
          id="feed-search"
          type="search"
          className={styles.search}
          placeholder="Например, юань или ставка"
          value={filters.query}
          onChange={(event) => set('query', event.target.value)}
        />
      </div>

      <div className={styles.group}>
        <label className={styles.legend} htmlFor="feed-period">
          Период
        </label>
        <select
          id="feed-period"
          className={styles.select}
          value={filters.period}
          onChange={(event) => set('period', event.target.value as Period)}
        >
          {periodOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <CheckboxGroup
        legend="Уровень влияния"
        options={impactOptions}
        selected={filters.impact}
        onChange={(value) => set('impact', value)}
      />
      <CheckboxGroup
        legend="Тип"
        options={typeOptions}
        selected={filters.types}
        onChange={(value) => set('types', value)}
      />
      <CheckboxGroup
        legend="Источник"
        options={sourceOptions}
        selected={filters.sources}
        onChange={(value) => set('sources', value)}
      />

      <button
        type="button"
        className={styles.reset}
        onClick={() => onChange({ ...defaultFilters, sort: filters.sort })}
        disabled={active === 0}
      >
        Сбросить фильтры{active > 0 ? ` (${active})` : ''}
      </button>
    </>
  );

  return (
    <aside className={styles.panel} aria-label="Фильтры ленты">
      {/* На широком экране панель открыта всегда, на узком — сворачивается */}
      <details className={styles.details} open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className={styles.summary}>
          Фильтры{active > 0 ? ` · ${active}` : ''}
        </summary>
        <div className={styles.body}>{content}</div>
      </details>
    </aside>
  );
}
