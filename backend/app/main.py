from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, companies, documents, feedback, health, insights, jobs
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging

settings = get_settings()
setup_logging(settings.log_level)

# Вход включён, а подписывать токены нечем — лучше не стартовать, чем отвечать 401 на всё
if settings.auth_enabled and not settings.jwt_secret:
    raise RuntimeError("AUTH_ENABLED=true, но JWT_SECRET не задан: задайте его в .env")

app = FastAPI(title=settings.app_name)

# CORS (разрешение браузеру фронтенда ходить в API с другого адреса)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(companies.router)
app.include_router(insights.router)
app.include_router(feedback.router)
app.include_router(jobs.router)
# служебный список собранных документов (internal, не для фронтенда)
app.include_router(documents.router)
