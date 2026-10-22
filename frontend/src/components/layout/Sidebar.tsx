import { NavLink } from 'react-router';
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

export function Sidebar({ insightsCount }: { insightsCount: number }) {
  return (
    <nav className={styles.sidebar} aria-label="Основное меню">
      <ul className={styles.list}>
        {items.map((item) => (
          <li key={item.to}>
            <NavLink to={item.to} end={item.to === '/'} className={linkClass}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>

      {/* Постоянная точка входа в сервис — видна в меню на любом экране (критерий 09) */}
      <div className={styles.divider} role="presentation" />
      <NavLink to="/analytics" className={({ isActive }) => `${linkClass({ isActive })} ${styles.insights}`}>
        <Icon name="spark" />
        <span className={styles.insightsText}>Новостная аналитика</span>
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
