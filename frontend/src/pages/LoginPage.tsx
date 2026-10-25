import { useState, type FormEvent } from 'react';
import { login } from '../api/client';
import { setToken } from '../api/token';
import { AlfaLogo } from '../components/ui/AlfaLogo';
import styles from './LoginPage.module.css';

// Тестовые учётные записи из README бэкенда. Пароль у всех один — DEMO_USER_PASSWORD сервера,
// его передают эксперту отдельно, поэтому в интерфейсе его нет.
const demoAccounts = [
  { email: 'cfo.stroy@news2value.demo', label: 'Финдиректор, АО «Демо-Девелопмент»' },
  { email: 'owner.stroy@news2value.demo', label: 'Собственник, АО «Демо-Девелопмент»' },
  { email: 'treasurer.azs@news2value.demo', label: 'Казначей, сеть АЗС' },
  { email: 'admin@news2value.demo', label: 'Администратор, все компании' },
];

/** Вход в интернет-банк (POST /auth/login). Показывается, только если на сервере включён вход. */
export function LoginPage({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await login(email.trim(), password);
      setToken(result.access_token);
      onLoggedIn();
    } catch (err) {
      const status = (err as { status?: number }).status;
      setError(status === 401 ? 'Неверный email или пароль' : 'Не удалось войти. Проверьте, что сервер запущен.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={styles.page}>
      <form className={styles.card} onSubmit={submit} aria-labelledby="login-title">
        <div className={styles.brand}>
          <AlfaLogo size={44} />
          <div>
            <h1 id="login-title" className={styles.title}>
              Альфа<span className={styles.accent}>Бизнес</span>
            </h1>
            <p className={styles.caption}>Вход в интернет-банк</p>
          </div>
        </div>

        <label className={styles.field}>
          <span>Email</span>
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span>Пароль</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>

        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}

        <button type="submit" className={styles.submit} disabled={busy}>
          {busy ? 'Входим…' : 'Войти'}
        </button>

        <div className={styles.demo}>
          <p className={styles.demoTitle}>Тестовые пользователи (пароль выдаёт команда):</p>
          <ul className={styles.demoList}>
            {demoAccounts.map((account) => (
              <li key={account.email}>
                <button type="button" className={styles.demoButton} onClick={() => setEmail(account.email)}>
                  <span className={styles.demoEmail}>{account.email}</span>
                  <span className={styles.demoLabel}>{account.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </form>
    </main>
  );
}
