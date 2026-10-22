import { Link } from 'react-router';
import type { Company, CompanyProfile, Insight } from '../../types';
import { formatDate, pluralEvents } from '../../utils/format';
import { impactLevel, insightOrigins } from '../../utils/insight';
import { Icon } from '../ui/Icon';
import { ImpactPill, OriginChip } from '../ui/Tags';
import styles from './InsightsEntryCard.module.css';

interface Props {
  company: Company;
  profile: CompanyProfile;
  topInsights: Insight[];
  total: number;
}

/** Главная точка входа в сервис на главном экране Альфа-Бизнеса (критерий 09). */
export function InsightsEntryCard({ company, profile, topInsights, total }: Props) {
  const profileFacts = [
    company.industry,
    `выручка ${company.revenue_bln_rub.toLocaleString('ru-RU')} млрд ₽`,
    ...profile.bank_products.slice(0, 2),
    profile.currency_risks[0],
  ].filter(Boolean);

  return (
    <section className={styles.card} aria-labelledby="insights-title">
      <div className={styles.head}>
        <p className={styles.eyebrow}>
          <Icon name="spark" size={16} />
          Новостная аналитика · AI + эксперты Альфа-Банка
        </p>
        <h2 id="insights-title" className={styles.title}>
          {pluralEvents(total)}, которые могут повлиять на ваш бизнес
        </h2>
        <p className={styles.subtitle}>
          Не пересказ новостей, а объяснение: что это значит именно для вашей компании, какие риски
          и возможности, и что думает банк.
        </p>
        <p className={styles.profile}>
          <span className={styles.profileLabel}>Подобрано под профиль:</span> {profileFacts.join(' · ')}
        </p>
      </div>

      {topInsights.length > 0 ? (
        <ol className={styles.list}>
          {topInsights.map((insight) => (
            <li key={insight.id} className={styles.item}>
              <div className={styles.itemTop}>
                <ImpactPill level={impactLevel(insight.importance)} />
                <time className={styles.date} dateTime={insight.created_at}>
                  {formatDate(insight.created_at)}
                </time>
              </div>
              <h3 className={styles.itemTitle}>{insight.title}</h3>
              <p className={styles.why}>
                <span className={styles.whyLabel}>Почему важно вам: </span>
                {insight.impact_on_company}
              </p>
              <div className={styles.sources} aria-label="Происхождение выводов">
                {insightOrigins(insight).map((kind) => (
                  <OriginChip key={kind} kind={kind} />
                ))}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className={styles.empty}>Значимых событий для компании пока нет.</p>
      )}

      <div className={styles.footer}>
        <Link to="/analytics" className={styles.cta}>
          Открыть аналитику
          <Icon name="arrowRight" size={18} />
        </Link>
        <p className={styles.updated}>Каждый вывод со ссылкой на источник</p>
      </div>
    </section>
  );
}
