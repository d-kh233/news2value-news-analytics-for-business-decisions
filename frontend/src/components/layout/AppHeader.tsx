import { Link } from 'react-router';
import { useCurrentCompany } from '../../state/CurrentCompany';
import type { UserProfile } from '../../types';
import { AlfaLogo } from '../ui/AlfaLogo';
import styles from './AppHeader.module.css';

function initials(name: string): string {
  return name
    .split(' ')
    .map((part) => part[0])
    .slice(0, 2)
    .join('');
}

export function AppHeader({ user }: { user?: UserProfile }) {
  const { companies, company, selectCompany } = useCurrentCompany();

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
          </label>
        )}
        {user && (
          <div className={styles.user}>
            <span className={styles.avatar} aria-hidden="true">
              {initials(user.name)}
            </span>
            <span className={styles.userText}>
              <span className={styles.value}>{user.name}</span>
              <span className={styles.label}>{user.role}</span>
            </span>
          </div>
        )}
      </div>
    </header>
  );
}
