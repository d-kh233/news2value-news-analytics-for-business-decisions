# Фронтенд для сервера: сборка React → статика в nginx, запросы /api/... nginx отдаёт в API.
# Контекст сборки — корень репозитория (см. deploy/docker-compose.prod.yml).
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# На сервере фронт работает с настоящим API через тот же адрес (nginx: /api/ → api:8000).
# Переменные окружения сборки главнее frontend/.env, поэтому локальный .env с моками не помешает.
ENV VITE_USE_MOCKS=false \
    VITE_API_BASE_URL=/api
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
