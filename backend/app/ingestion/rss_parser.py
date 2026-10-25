"""RSS-ленты -> Draft (kind=external_news).

Лента читается feedparser, полный текст статьи — trafilatura (вырезает меню, рекламу,
комментарии). Если статью скачать нельзя (robots.txt запрещает, ошибка HTTP, таймаут) или
trafilatura не нашла текст, берём описание из ленты и ставим тег text_from_summary.
Нет и описания — документ со статусом error.

PoliteClient — «вежливый» HTTP-клиент: проверяет robots.txt (правила сайта для роботов),
выдерживает паузу между запросами к одному сайту и ограничивает время ответа (таймаут).

robots.txt разбирает RobotsRules по стандарту RFC 9309. urllib.robotparser не подходит:
он не понимает шаблоны * и $, а строку «Disallow: /?» читает как «Disallow: /» — запрет
всего сайта (так он закрывал kommersant.ru целиком).
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import feedparser
import httpx
import trafilatura

from app.ingestion.normalizer import Draft, clean_text, strip_html

logger = logging.getLogger(__name__)

KIND = "external_news"
SUMMARY_TAG = "text_from_summary"


class Disallowed(Exception):
    """robots.txt сайта запрещает скачивать этот адрес."""


class FeedError(Exception):
    """Ответ не похож на RSS/Atom-ленту."""


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class RobotsRules:
    """Правила robots.txt для одного робота.

    rules — (разрешено?, шаблон пути). В шаблоне * — любые символы, $ в конце — конец адреса.
    Действует самое длинное подходящее правило; при равной длине Allow главнее Disallow.
    """

    rules: list[tuple[bool, str]] = field(default_factory=list)
    crawl_delay: float | None = None
    # почему запрещено всё (robots.txt недоступен) — попадает в текст ошибки
    reason: str = ""

    @classmethod
    def allow_all(cls) -> "RobotsRules":
        return cls()

    @classmethod
    def deny_all(cls, reason: str = "") -> "RobotsRules":
        return cls(rules=[(False, "/")], reason=reason)

    @classmethod
    def parse(cls, text: str, user_agent: str) -> "RobotsRules":
        """Правила группы нашего робота, а если её нет — группы «*».

        Робот узнаётся по имени без версии: «News2ValueBot/0.1 (+url)» -> news2valuebot.
        """
        groups: list[_Group] = []
        current: _Group | None = None
        agent_line_last = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                # подряд идущие User-agent относятся к одной группе
                if current is None or not agent_line_last:
                    current = _Group()
                    groups.append(current)
                current.agents.append(value.lower())
                agent_line_last = True
                continue
            agent_line_last = False
            if current is None:
                continue
            if key in ("allow", "disallow") and value:
                current.rules.append((key == "allow", value))
            elif key == "crawl-delay":
                try:
                    current.crawl_delay = float(value)
                except ValueError:
                    pass
        name = user_agent.split("/", 1)[0].strip().lower()
        mine = [g for g in groups if name in g.agents] or [g for g in groups if "*" in g.agents]
        delays = [g.crawl_delay for g in mine if g.crawl_delay is not None]
        return cls(
            rules=[rule for g in mine for rule in g.rules],
            crawl_delay=delays[0] if delays else None,
        )

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        regex = ".*".join(re.escape(part) for part in body.split("*"))
        return re.match(regex + ("$" if anchored else ""), path) is not None

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if path == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if self._matches(pattern, path):
                # длиннее — главнее; при равной длине True (Allow) > False (Disallow)
                candidate = (len(pattern), allow)
                best = candidate if best is None else max(best, candidate)
        return best is None or best[1]


class PoliteClient:
    """HTTP-клиент с robots.txt, паузой между запросами к одному сайту и таймаутом.

    transport, sleep и clock подменяются в тестах (без интернета и без настоящего ожидания).
    """

    def __init__(
        self,
        user_agent: str,
        timeout_s: float = 10.0,
        delay_s: float = 1.0,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = user_agent
        self.delay_s = delay_s
        self._http = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, RobotsRules] = {}
        self._last_request: dict[str, float] = {}

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _wait(self, origin: str) -> None:
        """Пауза перед запросом: не чаще delay_s (или Crawl-delay из robots.txt, если больше)."""
        delay = self.delay_s
        robots = self._robots.get(origin)
        if robots is not None:
            delay = max(delay, robots.crawl_delay or 0.0)
        last = self._last_request.get(origin)
        if last is not None:
            left = delay - (self._clock() - last)
            if left > 0:
                self._sleep(left)
        self._last_request[origin] = self._clock()

    def _load_robots(self, origin: str) -> RobotsRules:
        """robots.txt сайта. Нет файла (4xx) — можно всё; сайт недоступен (5xx, сеть) — нельзя."""
        self._wait(origin)
        try:
            resp = self._http.get(f"{origin}/robots.txt")
        except httpx.HTTPError as exc:
            logger.warning("robots.txt %s недоступен (%s): сайт пропускаем", origin, exc)
            return RobotsRules.deny_all(f"robots.txt недоступен: {type(exc).__name__}: {exc}")
        if resp.status_code >= 500:
            return RobotsRules.deny_all(f"robots.txt недоступен: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            return RobotsRules.allow_all()
        return RobotsRules.parse(resp.text, self.user_agent)

    def allowed(self, url: str) -> bool:
        origin = self._origin(url)
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        return self._robots[origin].allowed(url)

    def get(self, url: str) -> httpx.Response:
        """GET с проверкой robots.txt и паузой. Ошибки сети — httpx.HTTPError."""
        if not self.allowed(url):
            reason = self._robots[self._origin(url)].reason
            raise Disallowed(f"{url} ({reason})" if reason else url)
        self._wait(self._origin(url))
        return self._http.get(url)


@dataclass
class FeedEntry:
    """Запись ленты: ссылка на статью и то, что о ней сказано в самой ленте."""

    url: str
    title: str
    summary: str
    published: Any
    tags: list[str] = field(default_factory=list)


def read_feed(client: PoliteClient, url: str, max_items: int) -> list[FeedEntry]:
    """Записи ленты (не больше max_items). Лента недоступна или битая — исключение."""
    resp = client.get(url)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    # bozo — feedparser нашёл ошибки разметки; без записей это значит «не лента»
    if parsed.bozo and not parsed.entries:
        raise FeedError(f"Лента не разобрана: {parsed.get('bozo_exception')}")
    entries = []
    for e in parsed.entries[:max_items]:
        link = (e.get("link") or "").strip()
        if not link:
            continue
        entries.append(
            FeedEntry(
                url=link,
                title=e.get("title") or "",
                summary=e.get("summary") or "",
                published=e.get("published_parsed")
                or e.get("updated_parsed")
                or e.get("published"),
                tags=[t.get("term", "") for t in e.get("tags", [])],
            )
        )
    return entries


def _extract(client: PoliteClient, url: str) -> str:
    """Полный текст статьи или пустая строка (с записью причины в лог)."""
    try:
        resp = client.get(url)
    except Disallowed:
        logger.info("robots.txt запрещает %s: берём описание из ленты", url)
        return ""
    except httpx.HTTPError as exc:
        logger.warning("Статья %s не скачалась: %s", url, exc)
        return ""
    if resp.status_code != 200:
        logger.warning("Статья %s: HTTP %s", url, resp.status_code)
        return ""
    try:
        text = trafilatura.extract(
            resp.content, url=url, include_comments=False, include_tables=False
        )
    except Exception as exc:  # trafilatura падает на редком «сломанном» HTML
        logger.warning("Статья %s: текст не извлёкся: %s", url, exc)
        return ""
    return text or ""


def fetch_article(
    client: PoliteClient, entry: FeedEntry, source_name: str, tags: list[str] | None = None
) -> Draft:
    """Запись ленты -> Draft с полным текстом статьи (или описанием из ленты)."""
    draft = Draft(
        kind=KIND,
        source_name=source_name,
        url=entry.url,
        title=strip_html(entry.title),
        text="",
        published_at=entry.published,
        tags=[*(tags or []), *entry.tags],
        fallback_key=entry.url,
    )
    text = _extract(client, entry.url)
    if not text:
        summary = clean_text(strip_html(entry.summary))
        if summary:
            text = summary
            draft.tags.append(SUMMARY_TAG)
        else:
            draft.status = "error"
    draft.text = text
    return draft
