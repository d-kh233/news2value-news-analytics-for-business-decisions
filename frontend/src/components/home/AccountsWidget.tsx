import { Link } from 'react-router';
import type { Account } from '../../types';
import { formatMoney } from '../../utils/format';
import { Icon, type IconName } from '../ui/Icon';
import styles from './AccountsWidget.module.css';

export function AccountsWidget({ accounts }: { accounts: Account[] }) {
  return (
    <section className={styles.panel} aria-labelledby="accounts-title">
      <div className={styles.panelHead}>
        <h2 id="accounts-title" className={styles.panelTitle}>
          Счета
        </h2>
        <Link to="/accounts" className={styles.more}>
          Все счета
        </Link>
      </div>
      <ul className={styles.accounts}>
        {accounts.map((account) => (
          <li key={account.id} className={styles.account}>
            <span className={styles.accountName}>
              {account.title} <span className={styles.muted}>{account.maskedNumber}</span>
            </span>
            <span className={styles.balance}>{formatMoney(account.balance, account.currency)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const actions: { to: string; label: string; icon: IconName }[] = [
  { to: '/payments', label: 'Создать платёж', icon: 'send' },
  { to: '/accounts', label: 'Выписка', icon: 'doc' },
  { to: '/credits', label: 'Кредит или гарантия', icon: 'shield' },
];

export function QuickActions() {
  return (
    <section className={styles.panel} aria-labelledby="actions-title">
      <h2 id="actions-title" className={styles.panelTitle}>
        Быстрые действия
      </h2>
      <ul className={styles.actions}>
        {actions.map((action) => (
          <li key={action.label}>
            <Link to={action.to} className={styles.action}>
              <span className={styles.actionIcon}>
                <Icon name={action.icon} />
              </span>
              {action.label}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
