import { useEffect, useRef, useState, type FormEvent } from 'react';
import { askQuestion, listQuestions, waitForJob } from '../../api/client';
import type { Question } from '../../types';
import { formatTime } from '../../utils/format';
import { OriginChip } from '../ui/Tags';
import styles from './ChatPanel.module.css';

const MAX_LENGTH = 500;

const suggestions = [
  'Как это скажется на нашей долговой нагрузке?',
  'Что сделать в ближайший месяц?',
  'Какие продукты банка помогут снизить риск?',
];

/** Уточняющие вопросы по событию (критерий 10): POST /insights/{id}/ask → задача → ответ в истории. */
export function ChatPanel({ insightId }: { insightId: string }) {
  const [items, setItems] = useState<Question[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLOListElement>(null);

  async function reload() {
    try {
      const page = await listQuestions(insightId);
      setItems(page.items);
    } catch {
      setError('Не удалось загрузить историю вопросов.');
    }
  }

  useEffect(() => {
    setItems([]);
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [insightId]);

  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView({ block: 'nearest' });
  }, [items]);

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { job_id } = await askQuestion(insightId, trimmed);
      setText('');
      await reload();
      const job = await waitForJob(job_id, 1000, 3 * 60 * 1000);
      if (job.status === 'failed') setError('Не удалось подготовить ответ. Попробуйте ещё раз.');
      await reload();
    } catch (err) {
      const status = (err as { status?: number }).status;
      setError(
        status === 409
          ? 'Лимит вопросов к этому событию исчерпан (20).'
          : status === 404
            ? 'Событие обновилось — вернитесь к ленте и откройте его заново.'
            : 'Не удалось отправить вопрос. Проверьте, что бэкенд запущен.',
      );
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(text);
  }

  return (
    <section className={styles.panel} aria-labelledby="chat-title">
      <h2 id="chat-title" className={styles.title}>
        Задать уточняющий вопрос
      </h2>
      <p className={styles.hint}>AI-помощник ответит с учётом этой карточки, данных компании и источников.</p>

      {items.length > 0 && (
        <ol className={styles.messages} ref={listRef} aria-live="polite">
          {items.map((item) => (
            <li key={item.id} className={styles.pair}>
              <p className={styles.question}>
                {item.question}
                <time className={styles.time} dateTime={item.created_at}>
                  {formatTime(item.created_at)}
                </time>
              </p>
              <div className={styles.answer}>
                {item.status === 'done' && item.answer ? (
                  <>
                    <OriginChip kind="ai" />
                    <p className={styles.answerText}>{item.answer}</p>
                    {item.sources.length > 0 && (
                      <ul className={styles.sources}>
                        {item.sources.map((source, index) => (
                          <li key={`${source.url}-${index}`}>
                            {/^https?:\/\//.test(source.url) ? (
                              <a href={source.url} target="_blank" rel="noreferrer noopener">
                                {source.title}
                                <span className="visually-hidden"> (откроется в новой вкладке)</span>
                              </a>
                            ) : (
                              source.title
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                ) : item.status === 'failed' ? (
                  <p className={styles.failed}>Ответ не получился.</p>
                ) : (
                  <p className={styles.pending}>Готовим ответ…</p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}

      {items.length === 0 && (
        <div className={styles.suggestions}>
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className={styles.suggestion}
              onClick={() => void ask(suggestion)}
              disabled={busy}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}

      <form className={styles.form} onSubmit={submit}>
        <label htmlFor="chat-input" className="visually-hidden">
          Ваш вопрос
        </label>
        <textarea
          id="chat-input"
          className={styles.input}
          rows={2}
          maxLength={MAX_LENGTH}
          placeholder="Например: как это повлияет на закупки?"
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void ask(text);
            }
          }}
        />
        <button type="submit" className={styles.send} disabled={busy || !text.trim()}>
          {busy ? 'Ждём ответ…' : 'Спросить'}
        </button>
      </form>
    </section>
  );
}
