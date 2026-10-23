import { Link } from 'react-router';
import type { Insight } from '../../types';
import { formatDate } from '../../utils/format';
import { impactLevel, insightOrigins } from '../../utils/insight';
import { whyItMattersText } from '../../utils/labels';
import { Icon } from '../ui/Icon';
import { ImpactPill, OriginChip, TypeTag } from '../ui/Tags';
import styles from './InsightCard.module.css';

const sourceKindLabel = {
  external: 'Новость',
  bank_analytics: 'Аналитика банка',
} as const;

/** Карточка события в ленте. */
export function InsightCard({ insight }: { insight: Insight }) {
  const titleId = `insight-${insight.id}-title`;

  return (
    <article className={styles.card} aria-labelledby={titleId}>
      <div className={styles.top}>
        <div className={styles.tags}>
          <ImpactPill level={impactLevel(insight.importance)} />
          <TypeTag type={insight.type} />
        </div>
        <time className={styles.date} dateTime={insight.created_at}>
          {formatDate(insight.created_at)}
        </time>
      </div>

      <h2 id={titleId} className={styles.title}>
        <Link to={`/analytics/${insight.id}`} className={styles.titleLink}>
          {insight.title}
        </Link>
      </h2>
      <p className={styles.summary}>{insight.summary}</p>

      <div className={styles.why}>
        <p className={styles.whyLabel}>Почему важно вашей компании</p>
        <p>{whyItMattersText(insight)}</p>
      </div>

      <div className={styles.bottom}>
        <div className={styles.origins} aria-label="Происхождение выводов">
          {insightOrigins(insight).map((kind) => (
            <OriginChip key={kind} kind={kind} />
          ))}
        </div>
        <Link to={`/analytics/${insight.id}`} className={styles.more} aria-hidden="true" tabIndex={-1}>
          Подробнее
          <Icon name="arrowRight" size={16} />
        </Link>
      </div>

      {/* Переход к первоисточнику (критерий 10) */}
      <details className={styles.sources}>
        <summary className={styles.sourcesSummary}>Источники ({insight.sources.length})</summary>
        <ul className={styles.sourceList}>
          {insight.sources.map((source, index) => (
            <li key={`${source.url}-${index}`}>
              <span className={styles.sourceKind}>{sourceKindLabel[source.source_kind]}</span>
              {/^https?:\/\//.test(source.url) ? (
                <a href={source.url} target="_blank" rel="noreferrer noopener" className={styles.sourceLink}>
                  {source.title}
                  <span className="visually-hidden"> (откроется в новой вкладке)</span>
                </a>
              ) : (
                <span>{source.title}</span>
              )}
              <span className={styles.sourceDate}>{formatDate(source.published_at)}</span>
            </li>
          ))}
        </ul>
      </details>
    </article>
  );
}
