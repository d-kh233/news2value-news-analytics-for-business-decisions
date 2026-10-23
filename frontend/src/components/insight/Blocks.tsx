import type { ReactNode } from 'react';
import type { Statement } from '../../types';
import { asSentence, sourceIdLabel } from '../../utils/labels';
import { OriginChip } from '../ui/Tags';
import styles from './Blocks.module.css';

/** Пронумерованный блок карточки события (01–06). */
export function Block({
  number,
  title,
  children,
}: {
  number: string;
  title: string;
  children: ReactNode;
}) {
  const id = `block-${number}`;
  return (
    <section className={styles.block} aria-labelledby={id}>
      <h2 id={id} className={styles.blockTitle}>
        <span className={styles.number} aria-hidden="true">
          {number}
        </span>
        {title}
      </h2>
      <div className={styles.blockBody}>{children}</div>
    </section>
  );
}

/** Ссылки утверждения на признаки компании и документы — короткими метками. */
export function SourceRefs({ ids }: { ids: string[] }) {
  const labels = [...new Set(ids.map(sourceIdLabel))];
  if (labels.length === 0) return null;
  return (
    <p className={styles.refs}>
      <span className={styles.refsLabel}>Опирается на:</span> {labels.join(' · ')}
    </p>
  );
}

/** Список утверждений с меткой происхождения у каждого. */
export function StatementList({ statements }: { statements: Statement[] }) {
  return (
    <ul className={styles.statements}>
      {statements.map((statement, index) => (
        <li key={index} className={styles.statement}>
          <OriginChip kind={statement.origin} />
          <div>
            <p>{asSentence(statement.text)}</p>
            <SourceRefs ids={statement.source_ids} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className={styles.empty}>{children}</p>;
}
