import type { Account, UserProfile } from '../types';

// Данные «обычного» интернет-банка, которых нет в API бэкенда.
// Компании, профили и инсайты лежат рядом в *.json — это копия data/mocks из ветки feat/backend.

export const mockUser: UserProfile = {
  name: 'Анна Кузнецова',
  role: 'Финансовый директор',
};

export const mockAccounts: Account[] = [
  {
    id: 'acc-rub',
    title: 'Расчётный счёт',
    currency: 'RUB',
    balance: 1284530912.4,
    maskedNumber: '•• 4821',
  },
  {
    id: 'acc-cny',
    title: 'Валютный счёт',
    currency: 'CNY',
    balance: 12450300,
    maskedNumber: '•• 7710',
  },
];
