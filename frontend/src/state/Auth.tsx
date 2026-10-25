import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { getMe } from '../api/client';
import { clearToken, onUnauthorized } from '../api/token';
import type { UserInfo } from '../types';

// Кто вошёл в систему (GET /auth/me). Если на сервере вход включён (auth_enabled) и токена нет
// или он истёк, приложение показывает форму входа вместо интерфейса.

type AuthState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'login' }
  | { status: 'ready'; user: UserInfo };

interface AuthValue {
  user: UserInfo;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuthState() {
  const [state, setState] = useState<AuthState>({ status: 'loading' });

  function load() {
    setState({ status: 'loading' });
    getMe()
      .then((user) => setState({ status: 'ready', user }))
      .catch((error: { status?: number }) => setState(error.status === 401 ? { status: 'login' } : { status: 'error' }));
  }

  useEffect(() => {
    load();
    return onUnauthorized(() => setState({ status: 'login' }));
  }, []);

  return { state, reload: load };
}

export function AuthProvider({ user, onLogout, children }: { user: UserInfo; onLogout: () => void; children: ReactNode }) {
  function logout() {
    clearToken();
    onLogout();
  }
  return <AuthContext.Provider value={{ user, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth нужно вызывать внутри AuthProvider');
  return value;
}
