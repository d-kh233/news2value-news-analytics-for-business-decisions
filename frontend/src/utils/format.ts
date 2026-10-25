import type { Account, Company } from '../types';

/**
 * Выручка для подписи. У компаний из данных банка единицы не подтверждены (контракт API),
 * поэтому «млрд ₽» пишем только для введённых вручную и моковых компаний.
 */
export function formatRevenue(company: Pick<Company, 'revenue_bln_rub' | 'revenue_known' | 'source'>): string {
  if (company.revenue_known === false) return 'выручка: нет данных';
  const value = company.revenue_bln_rub.toLocaleString('ru-RU');
  return company.source === 'bank' ? `выручка ${value} (по данным банка)` : `выручка ${value} млрд ₽`;
}

const currencySymbol: Record<Account['currency'], string> = {
  RUB: '₽',
  CNY: '¥',
  USD: '$',
  EUR: '€',
};

export function formatMoney(amount: number, currency: Account['currency']): string {
  const value = new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
  return `${value} ${currencySymbol[currency]}`;
}

export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(new Date(iso));
}

export function formatTime(iso: string): string {
  return new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
}

export function formatToday(): string {
  return new Intl.DateTimeFormat('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' }).format(new Date());
}

/** «7 событий», «1 событие», «3 события». */
export function pluralEvents(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} событие`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} события`;
  return `${count} событий`;
}
