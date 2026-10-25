import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router';
import { Icon, type IconName } from '../ui/Icon';
import styles from './Sidebar.module.css';

const items: { to: string; label: string; icon: IconName }[] = [
  { to: '/', label: 'Главная', icon: 'home' },
  { to: '/accounts', label: 'Счета', icon: 'wallet' },
  { to: '/payments', label: 'Платежи', icon: 'send' },
  { to: '/credits', label: 'Кредиты и гарантии', icon: 'credit' },
  { to: '/ved', label: 'ВЭД', icon: 'globe' },
];

function linkClass({ isActive }: { isActive: boolean }) {
  return isActive ? `${styles.link} ${styles.active}` : styles.link;
}

/**
 * Основное меню. На широком экране — колонка слева, на телефоне — кнопка «Меню»
 * с выпадающим списком. «Новостная аналитика» видна всегда (критерий 09).
 */
export function Sidebar({ insightsCount }: { insightsCount: number }) {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // Закрываем выпадающее меню после перехода и по Escape
  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <nav className={styles.sidebar} aria-label="Основное меню">
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        aria-controls="main-menu"
        onClick={() => setOpen((value) => !value)}
      >
        <span className={styles.burger} aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        Меню
      </button>

      <ul id="main-menu" className={`${styles.list} ${open ? styles.listOpen : ''}`}>
        {items.map((item) => (
          <li key={item.to}>
            <NavLink to={item.to} end={item.to === '/'} className={linkClass}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>

      {/* Постоянная точка входа в сервис — видна на любом экране (критерий 09) */}
      <div className={styles.divider} role="presentation" />
      <NavLink to="/analytics" className={({ isActive }) => `${linkClass({ isActive })} ${styles.insights}`}>
        <Icon name="spark" />
        <span className={styles.insightsText}>
          <span className={styles.longLabel}>Новостная аналитика</span>
          <span className={styles.shortLabel} aria-hidden="true">
            Аналитика
          </span>
        </span>
        {insightsCount > 0 ? (
          <span className={styles.badge}>
            {insightsCount}
            <span className="visually-hidden"> событий для компании</span>
          </span>
        ) : (
          <span className={styles.badge}>Новое</span>
        )}
      </NavLink>
    </nav>
  );
}
