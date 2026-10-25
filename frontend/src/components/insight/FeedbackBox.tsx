import { useEffect, useState } from 'react';
import { getMyFeedback, sendFeedback } from '../../api/client';
import type { FeedbackRating, FeedbackReason } from '../../types';
import styles from './FeedbackBox.module.css';

const reasons: { value: FeedbackReason; label: string }[] = [
  { value: 'irrelevant', label: 'Не про нас' },
  { value: 'inaccurate', label: 'Неточно' },
  { value: 'unclear', label: 'Непонятно' },
  { value: 'other', label: 'Другое' },
];

/** Оценка карточки пользователем — для метрик качества (критерий 08). */
export function FeedbackBox({ insightId }: { insightId: string }) {
  const [rating, setRating] = useState<FeedbackRating | null>(null);
  const [reason, setReason] = useState<FeedbackReason | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(false);

  // Подсветить уже выбранную оценку
  useEffect(() => {
    setRating(null);
    setReason(null);
    setSaved(false);
    getMyFeedback(insightId)
      .then((feedback) => {
        if (!feedback) return;
        setRating(feedback.rating);
        setReason(feedback.reason ?? null);
        setSaved(true);
      })
      .catch(() => undefined);
  }, [insightId]);

  async function rate(value: FeedbackRating, why?: FeedbackReason) {
    setError(false);
    try {
      await sendFeedback(insightId, value, why);
      setRating(value);
      setReason(why ?? null);
      setSaved(true);
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
          onClick={() => rate('not_useful', reason ?? undefined)}
        >
          Нет
        </button>
      </div>

      {rating === 'not_useful' && (
        <div className={styles.reasons} role="group" aria-label="Что не так">
          <span className={styles.reasonsLabel}>Что не так?</span>
          {reasons.map((item) => (
            <button
              key={item.value}
              type="button"
              className={styles.reason}
              aria-pressed={reason === item.value}
              onClick={() => rate('not_useful', item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}

      <p className={styles.note} role="status" aria-live="polite">
        {error
          ? 'Не удалось отправить оценку, попробуйте ещё раз.'
          : saved
            ? 'Спасибо! Оценки помогают улучшать подбор и объяснения.'
            : 'Оценка поможет точнее подбирать события для вашей компании.'}
      </p>
    </section>
  );
}
