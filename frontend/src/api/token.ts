// Токен входа (B8+B9). Хранится в localStorage, чтобы вход переживал перезагрузку страницы.
// Если сервер ответил 401, токен стирается и приложение снова показывает форму входа.

const TOKEN_KEY = 'news2value.token';

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // без localStorage вход просто не переживёт перезагрузку
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // ничего
  }
}

/** Подписка на «сервер ответил 401» — чтобы показать форму входа. */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export function notifyUnauthorized(): void {
  clearToken();
  unauthorizedListeners.forEach((listener) => listener());
}
