import { Link } from 'react-router';
import { getCurrentUser } from '../../api/client';
import { useAsync } from '../../api/useAsync';
import { useAuth } from '../../state/Auth';
import { useCurrentCompany } from '../../state/CurrentCompany';
import type { UserRole } from '../../types';
import { AlfaLogo } from '../ui/AlfaLogo';
import styles from './AppHeader.module.css';

const userRoleLabel: Record<UserRole, string> = {
  owner: 'Собственник',
  ceo: 'Генеральный директор',
  cfo: 'Финансовый директор',
  treasurer: 'Казначейство',
  admin: 'Администратор',
};

function initials(name: string): string {
  return name
    .split(/[\s.@]+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase())
    .slice(0, 2)
    .join('');
}

export function AppHeader() {
  const { companies, company, selectCompany } = useCurrentCompany();
  const { user, logout } = useAuth();
  const demoProfile = useAsync(getCurrentUser, []);

  // Вход включён — показываем того, кто вошёл; выключен — демо-пользователя интернет-банка
  const person = user.auth_enabled
    ? { name: user.email ?? user.id, role: user.role ? userRoleLabel[user.role] : '' }
    : demoProfile.status === 'success'
      ? { name: demoProfile.data.name, role: demoProfile.data.role }
      : null;

  return (
    <header className={styles.header}>
      <Link to="/" className={styles.brand} aria-label="Альфа-Бизнес, на главную">
        <AlfaLogo />
        <span className={styles.brandText} aria-hidden="true">
          <span className={styles.brandName}>
            Альфа<span className={styles.brandAccent}>Бизнес</span>
          </span>
          <span className={styles.brandCaption}>интернет-банк</span>
        </span>
      </Link>

      <div className={styles.right}>
        {company && (
          <label className={styles.company}>
            <span className={styles.label}>Компания</span>
            {companies.length > 1 ? (
              <select
                className={styles.select}
                aria-label="Компания-клиент"
                value={company.id}
                onChange={(event) => selectCompany(event.target.value)}
              >
                {companies.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            ) : (
              <span className={styles.value}>{company.name}</span>
            )}
          </label>
        )}
        {person && (
          <div className={styles.user}>
            <span className={styles.avatar} aria-hidden="true">
              {initials(person.name)}
            </span>
            <span className={styles.userText}>
              <span className={styles.value}>{person.name}</span>
              <span className={styles.label}>{person.role}</span>
            </span>
            {user.auth_enabled && (
              <button type="button" className={styles.logout} onClick={logout}>
                Выйти
              </button>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
