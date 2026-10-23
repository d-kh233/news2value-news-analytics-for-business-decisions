import type { Consequence, Insight, InsightAction, Level, Role } from '../types';

// Перевод служебных кодов из ответа агента в понятные пользователю подписи.

/** Колонки df_Uid_data_hack.csv (приложение 1 кейса) → человеческое название признака. */
const featureLabels: Record<string, string> = {
  MAINOKVED_CODE: 'Основной вид деятельности (ОКВЭД)',
  MAINOKVED_NAME: 'Основной вид деятельности (ОКВЭД)',
  REGION: 'Регион регистрации',
  CITY: 'Город регистрации',
  REVENUE_COMPANY: 'Годовая выручка',
  ORG_TYPE: 'Тип организации',
  OKFS_NAME: 'Форма собственности',
  COMPANY_AGE_YEARS_GRADATION: 'Возраст компании',
  ECO_MARK_LABEL: 'Стадия развития бизнеса',
  NEGATIVEMARK: 'Признак ключевого собственника',
  AMOUNT_WORKERS_FNS_GRADATION: 'Численность сотрудников (ФНС)',
  AMOUNT_WORKERS_SPARK_GRADATION: 'Численность сотрудников (СПАРК)',
  CL_TYPE: 'Тип клиента банка',
  CL_PRODUCT_RKO_MARK: 'Расчётно-кассовое обслуживание',
  CL_PRODUCT_GAR_MARK: 'Банковские гарантии',
  CL_PRODUCT_LOA_MARK: 'Кредитные продукты',
  CL_PRODUCT_LEA_MARK: 'Лизинг',
  CL_PRODUCT_DEP_MARK: 'Депозиты',
  CL_PRODUCT_ZPP_MARK: 'Зарплатный проект',
  FTS_FLAG: 'Внешнеэкономическая деятельность',
  FTS_COUNTRY_NAME: 'Страна внешнеэкономической деятельности',
  FACTORING_MARK: 'Факторинг',
  GROUP_ID: 'Группа компаний',
};

/** Подпись для id источника: F:<колонка>, P:<контрагент>, G:<группа>, N:<новость>, A:<аналитика>. */
export function sourceIdLabel(sourceId: string): string {
  const [prefix, ...rest] = sourceId.split(':');
  const value = rest.join(':');
  switch (prefix) {
    case 'F':
      return featureLabels[value] ?? value;
    case 'P':
      return `Платежи с контрагентом ${value}`;
    case 'G':
      return `Группа компаний ${value}`;
    case 'N':
      return 'Новость';
    case 'A':
      return 'Аналитика Альфа-Банка';
    default:
      return sourceId;
  }
}

/** Заменяет коды признаков в тексте («усилено признаками F:FTS_FLAG») на их названия. */
export function humanizeCodes(text: string): string {
  return text.replace(/\b([FPG]):([A-Za-z0-9_.-]+)/g, (match) => `«${sourceIdLabel(match)}»`);
}

/** Оформляет фразу как предложение: заглавная буква в начале, точка в конце, коды признаков — словами. */
export function asSentence(text: string): string {
  const trimmed = humanizeCodes(text.trim());
  if (!trimmed) return trimmed;
  const capitalized = trimmed[0].toUpperCase() + trimmed.slice(1);
  return /[.!?…]$/.test(capitalized) ? capitalized : `${capitalized}.`;
}

/** Текст «Почему важно» для ленты: из 6 блоков, если они есть, иначе из основного поля. */
export function whyItMattersText(insight: Insight): string {
  const statements = insight.details?.why_it_matters;
  if (statements && statements.length > 0) {
    return statements.map((s) => asSentence(s.text)).join(' ');
  }
  return asSentence(insight.impact_on_company.split(' Позиция банка:')[0]);
}

export const levelLabel: Record<Level, string> = {
  high: 'высокая',
  medium: 'средняя',
  low: 'низкая',
};

export const horizonLabel: Record<Consequence['horizon'], string> = {
  immediate: 'в ближайшие дни',
  short: 'до 3 месяцев',
  medium: '3–12 месяцев',
  long: 'больше года',
};

export const consequenceKindLabel: Record<Consequence['kind'], string> = {
  risk: 'Риски',
  opportunity: 'Возможности',
  factor: 'Что повлияет на ситуацию',
};

export const actionTypeLabel: Record<InsightAction['type'], string> = {
  action: 'Действие',
  question: 'Вопрос для анализа',
  scenario: 'Сценарий',
};

export const roleLabel: Record<Role, string> = {
  owner: 'собственника',
  ceo: 'генерального директора',
  cfo: 'финансового директора',
  treasurer: 'казначейства',
  accountant: 'главного бухгалтера',
  procurement: 'службы закупок',
};
