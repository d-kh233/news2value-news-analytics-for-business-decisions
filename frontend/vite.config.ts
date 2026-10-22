import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Явный IPv4-адрес: на Windows "localhost" иногда слушается только по IPv6
    host: '127.0.0.1',
    port: 5173,
    // Запросы фронта на /api/... уходят на бэкенд без префикса /api (http://127.0.0.1:8000/...).
    // Так браузер обращается к одному адресу и CORS не нужен.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
