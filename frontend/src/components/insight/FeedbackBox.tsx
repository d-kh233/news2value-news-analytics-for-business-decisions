import { useState } from 'react';
import { sendFeedback } from '../../api/client';
import type { FeedbackRating } from '../../types';
import styles from './FeedbackBox.module.css';

/** Оценка карточки пользователем — для метрик качества (критерий 08). */
export function FeedbackBox({ insightId }: { insightId: string }) {
  const [rating, setRating] = useState<FeedbackRating | null>(null);
  const [error, setError] = useState(false);

  async function rate(value: FeedbackRating) {
    setError(false);
    try {
      await sendFeedback(insightId, value);
      setRating(value);
    } catch {
      setError(true);
    }
  }

  return (
    <section className={styles.box} aria-labelledby="feedback-title">
      <h2 id="feedback-title" className={styles.title}>
        Разбор был полезен?
      </h2>
      <div className={styles.buttons}>
        <button
          type="button"
          className={styles.button}
          aria-pressed={rating === 'useful'}
          onClick={() => rate('useful')}
        >
          Да, полезно
        </button>
        <button
          type="button"
          className={styles.button}
          aria-pressed={rating === 'not_useful'}
          onClick={() => rate('not_useful')}
        >
          Нет
        </button>
      </div>
      <p className={styles.note} role="status" aria-live="polite">
        {error
          ? 'Не удалось отправить оценку, попробуйте ещё раз.'
          : rating
            ? 'Спасибо! Оценки помогают улучшать подбор и объяснения.'
            : 'Оценка поможет точнее подбирать события для вашей компании.'}
      </p>
    </section>
  );
}
