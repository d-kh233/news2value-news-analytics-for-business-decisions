import { Link, useParams } from 'react-router';
import { getInsight } from '../api/client';
import { useAsync } from '../api/useAsync';
import { Block, EmptyNote, InsightSourcesContext, SourceRefs, StatementList } from '../components/insight/Blocks';
import { ChatPanel } from '../components/insight/ChatPanel';
import { FeedbackBox } from '../components/insight/FeedbackBox';
import { TracePanel } from '../components/insight/TracePanel';
import { ImpactPill, OriginChip, TypeTag } from '../components/ui/Tags';
import type { Consequence, Insight, InsightDetails } from '../types';
import { formatDate } from '../utils/format';
import { impactLevel } from '../utils/insight';
import {
  actionTypeLabel,
  consequenceKindLabel,
  horizonLabel,
  asSentence,
  levelLabel,
  roleLabel,
  sourceIdLabel,
} from '../utils/labels';
import styles from './InsightPage.module.css';

const BANK_PREFIX = ' Позиция банка:';

/**
 * Краткая версия 6 блоков из основных полей — если агент ещё не разбирал событие
 * (в моках бэкенда поля details нет).
 */
function detailsFromBasic(insight: Insight): InsightDetails {
  const [why, bank] = insight.impact_on_company.split(BANK_PREFIX);
  const hasNews = insight.sources.some((s) => s.source_kind === 'external');
  return {
    news_id: '',
    company_id: insight.company_id,
    headline: insight.title,
    impact_type: insight.type === 'event' ? 'mixed' : insight.type,
    importance: impactLevel(insight.importance),
    confidence: 'medium',
    what_happened: [{ text: insight.summary, origin: hasNews ? 'fact' : 'ai', source_ids: [] }],
    why_it_matters: [{ text: why, origin: 'ai', source_ids: [] }],
    consequences: [],
    actions: insight.recommended_action
      ? [{ text: insight.recommended_action, type: 'action', bank_product: null, source_ids: [] }]
      : [],
    bank_view: bank ? [{ text: bank.trim(), origin: 'bank', source_ids: [] }] : [],
    company_features_used: insight.used_profile_fields ?? [],
    data_gaps: [],
  };
}

const consequenceOrder: Consequence['kind'][] = ['risk', 'opportunity', 'factor'];

export function InsightPage() {
  const { insightId = '' } = useParams();
  const state = useAsync(() => getInsight(insightId), [insightId]);

  if (state.status === 'loading') {
    return (
      <p className={styles.status} role="status">
        Загружаем событие…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <div className={styles.page}>
        <Link to="/analytics" className={styles.back}>
          ← К ленте
        </Link>
        <p className={styles.status} role="alert">
          Событие не найдено. Возможно, лента обновилась — вернитесь к ленте и выберите событие заново.
        </p>
      </div>
    );
  }

  const insight = state.data;
  const full = Boolean(insight.details);
  const d = insight.details ?? detailsFromBasic(insight);
  const features = [...new Set([...d.company_features_used, ...(insight.used_profile_fields ?? [])])];
  const relevance = d.agent?.relevance;
  const bankSources = insight.sources.filter((s) => s.source_kind === 'bank_analytics');

  return (
    <InsightSourcesContext.Provider value={insight.sources}>
      <div className={styles.page}>
        <Link to="/analytics" className={styles.back}>
          ← К ленте
        </Link>

        <header className={styles.header}>
          <div className={styles.meta}>
            <ImpactPill level={impactLevel(insight.importance)} />
            <TypeTag type={insight.type} />
            <time dateTime={insight.created_at} className={styles.date}>
              {formatDate(insight.created_at)}
            </time>
          </div>
          <h1 className={styles.title}>{d.headline || insight.title}</h1>
          {full && (
            <p className={styles.subline}>
              {insight.role && <>Разбор для {roleLabel[insight.role]} · </>}
              Уверенность вывода: {levelLabel[d.confidence]}
              {insight.generated_by === 'rules' && <> · собрано по правилам, без языковой модели</>}
            </p>
          )}

          <div className={styles.legend} aria-label="Как читать метки">
            <span className={styles.legendItem}>
              <OriginChip kind="fact" /> подтверждено источником
            </span>
            <span className={styles.legendItem}>
              <OriginChip kind="bank" /> позиция экспертов Альфа-Банка
            </span>
            <span className={styles.legendItem}>
              <OriginChip kind="ai" /> вывод модели
            </span>
          </div>

          {!full && (
            <p className={styles.notice}>
              Краткая версия: AI-агент ещё не разбирал это событие подробно. Нажмите «Обновить анализ» в ленте, чтобы
              получить полный разбор с рисками, возможностями и признаками компании.
            </p>
          )}
        </header>

        <div className={styles.columns}>
          <div className={styles.content}>
            <Block number="01" title="Что произошло">
              <StatementList statements={d.what_happened} />
            </Block>

            <Block number="02" title="Почему это важно для компании">
              <StatementList statements={d.why_it_matters} />
            </Block>

            <Block number="03" title="Возможные последствия">
              {d.consequences.length === 0 ? (
                <EmptyNote>Оценка рисков и возможностей появится после разбора агентом.</EmptyNote>
              ) : (
                consequenceOrder
                  .map((kind) => ({ kind, items: d.consequences.filter((c) => c.kind === kind) }))
                  .filter((group) => group.items.length > 0)
                  .map((group) => (
                    <div key={group.kind} className={styles.group}>
                      <h3 className={`${styles.groupTitle} ${styles[`kind-${group.kind}`]}`}>
                        {consequenceKindLabel[group.kind]}
                      </h3>
                      <ul className={styles.items}>
                        {group.items.map((c, index) => (
                          <li key={index} className={styles.item}>
                            <div className={styles.itemHead}>
                              <OriginChip kind={c.origin} />
                              <span className={styles.itemMeta}>
                                сила: {levelLabel[c.level]} · горизонт: {horizonLabel[c.horizon]}
                              </span>
                            </div>
                            <p>{asSentence(c.text)}</p>
                            <SourceRefs ids={c.source_ids} />
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))
              )}
            </Block>

            <Block number="04" title="Что можно сделать">
              {d.actions.length === 0 ? (
                <EmptyNote>Конкретных действий по этому событию не предлагается — достаточно наблюдать.</EmptyNote>
              ) : (
                <ul className={styles.items}>
                  {d.actions.map((action, index) => (
                    <li key={index} className={styles.item}>
                      <div className={styles.itemHead}>
                        <span className={styles.actionType}>{actionTypeLabel[action.type]}</span>
                        {action.bank_product && (
                          <span className={styles.product}>Продукт банка: {action.bank_product}</span>
                        )}
                      </div>
                      <p>{asSentence(action.text)}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Block>

            <Block number="05" title="Как оценивает банк">
              {d.bank_view.length > 0 ? (
                <StatementList statements={d.bank_view} />
              ) : bankSources.length > 0 ? (
                <>
                  <EmptyNote>
                    Отдельного вывода банка в разборе нет. По теме есть материалы аналитиков Альфа-Банка:
                  </EmptyNote>
                  <ul className={styles.items}>
                    {bankSources.map((source, index) => (
                      <li key={index} className={styles.item}>
                        <div className={styles.itemHead}>
                          <OriginChip kind="bank" />
                          <span className={styles.itemMeta}>{formatDate(source.published_at)}</span>
                        </div>
                        <p>{source.title}</p>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <EmptyNote>У аналитиков Альфа-Банка нет опубликованной позиции по этой теме.</EmptyNote>
              )}
            </Block>

            <Block number="06" title="На чём основан вывод">
              {features.length > 0 && (
                <div className={styles.group}>
                  <h3 className={styles.groupTitle}>Признаки компании, повлиявшие на вывод</h3>
                  <ul className={styles.features}>
                    {features.map((id) => (
                      <li key={id} className={styles.feature}>
                        {sourceIdLabel(id)}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className={styles.group}>
                <h3 className={styles.groupTitle}>Источники</h3>
                <ul className={styles.sources}>
                  {insight.sources.map((source, index) => {
                    const linkable = /^https?:\/\//.test(source.url);
                    return (
                      <li key={`${source.url}-${index}`} className={styles.source}>
                        <OriginChip kind={source.source_kind === 'bank_analytics' ? 'bank' : 'fact'} />
                        <span>
                          {linkable ? (
                            <a
                              href={source.url}
                              target="_blank"
                              rel="noreferrer noopener"
                              className={styles.sourceLink}
                            >
                              {source.title}
                              <span className="visually-hidden"> (откроется в новой вкладке)</span>
                            </a>
                          ) : (
                            source.title
                          )}
                          <span className={styles.sourceDate}> · {formatDate(source.published_at)}</span>
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>

              {d.data_gaps.length > 0 && (
                <div className={styles.group}>
                  <h3 className={styles.groupTitle}>Чего не хватило для уверенного вывода</h3>
                  <ul className={styles.gaps}>
                    {d.data_gaps.map((gap, index) => (
                      <li key={index}>{gap}</li>
                    ))}
                  </ul>
                </div>
              )}

              {relevance !== undefined && (
                <p className={styles.relevance}>
                  Релевантность для компании по оценке агента: {Math.round(relevance * 100)}%
                </p>
              )}
            </Block>

            <TracePanel insightId={insight.id} />
            <FeedbackBox insightId={insight.id} />
          </div>

          <aside className={styles.aside} aria-label="Вопросы по событию">
            <ChatPanel insightId={insight.id} />
          </aside>
        </div>
      </div>
    </InsightSourcesContext.Provider>
  );
}
