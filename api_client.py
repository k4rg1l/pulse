"""
OpenRouter Pulse - API Client
Handles all communication with OpenRouter API endpoints.
"""
import requests
import time
from dataclasses import dataclass, field
from typing import Optional
from PySide6.QtCore import QObject, Signal, Slot, QThread

from config import (
    API_KEY, API_KEY_ENDPOINT, MODELS_ENDPOINT,
    MODELS_COUNT_ENDPOINT, STATUS_URL,
)

BASE_URL = "https://openrouter.ai"
CREDITS_ENDPOINT = f"{BASE_URL}/api/v1/credits"
PROVIDERS_ENDPOINT = f"{BASE_URL}/api/v1/providers"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://github.com/openrouter-pulse",
    "X-OpenRouter-Title": "OpenRouter Pulse",
}


@dataclass
class KeyInfo:
    label: str = ""
    limit: Optional[float] = None
    limit_remaining: Optional[float] = None
    limit_reset: Optional[str] = None
    usage: float = 0.0
    usage_daily: float = 0.0
    usage_weekly: float = 0.0
    usage_monthly: float = 0.0
    is_free_tier: bool = False
    total_credits: float = 0.0
    total_usage: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def remaining(self):
        if self.limit_remaining is not None:
            return self.limit_remaining
        if self.total_credits > 0:
            return max(0, self.total_credits - self.total_usage)
        return None

    @property
    def credit_percent(self):
        rem = self.remaining
        if rem is not None and self.total_credits > 0:
            return rem / self.total_credits
        if self.limit is not None and self.limit > 0 and self.limit_remaining is not None:
            return self.limit_remaining / self.limit
        return 1.0

    @property
    def burn_rate_daily(self):
        return self.usage_daily if self.usage_daily > 0 else 0.0

    @property
    def burn_rate_hourly(self):
        return self.burn_rate_daily / 24.0 if self.burn_rate_daily > 0 else 0.0

    @property
    def days_remaining(self):
        rem = self.remaining
        if rem is not None and self.burn_rate_daily > 0:
            return rem / self.burn_rate_daily
        return float('inf')


@dataclass
class ModelInfo:
    id: str = ""
    name: str = ""
    pricing_prompt: float = 0.0
    pricing_completion: float = 0.0
    context_length: int = 0
    top_provider: str = ""

    @property
    def price_per_mtok_prompt(self):
        return self.pricing_prompt * 1_000_000

    @property
    def price_per_mtok_completion(self):
        return self.pricing_completion * 1_000_000


@dataclass
class ServiceStatus:
    chat_api: str = "unknown"
    data_api: str = "unknown"
    homepage: str = "unknown"
    overall: str = "unknown"


@dataclass
class ProviderInfo:
    name: str
    slug: str
    status_page_url: Optional[str] = None
    headquarters: Optional[str] = None


class APIClient:
    """Synchronous API client for OpenRouter."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.last_error: Optional[str] = None

    def get_key_info(self) -> Optional[KeyInfo]:
        try:
            resp = self.session.get(API_KEY_ENDPOINT, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", {})

            total_credits = 0.0
            total_usage = 0.0
            credits_resp = self.session.get(CREDITS_ENDPOINT, timeout=15)
            if credits_resp.status_code == 200:
                cdata = credits_resp.json().get("data", {})
                total_credits = cdata.get("total_credits", 0.0)
                total_usage = cdata.get("total_usage", 0.0)

            self.last_error = None
            return KeyInfo(
                label=data.get("label", ""),
                limit=data.get("limit"),
                limit_remaining=data.get("limit_remaining"),
                limit_reset=data.get("limit_reset"),
                usage=data.get("usage", 0.0),
                usage_daily=data.get("usage_daily", 0.0),
                usage_weekly=data.get("usage_weekly", 0.0),
                usage_monthly=data.get("usage_monthly", 0.0),
                is_free_tier=data.get("is_free_tier", False),
                total_credits=total_credits,
                total_usage=total_usage,
                raw=data,
            )
        except requests.exceptions.ConnectionError:
            self.last_error = "No network"
        except requests.exceptions.Timeout:
            self.last_error = "Request timed out"
        except requests.exceptions.HTTPError as e:
            self.last_error = f"HTTP {e.response.status_code if e.response else '?'}"
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
        return None

    def get_providers(self) -> list:
        try:
            resp = self.session.get(PROVIDERS_ENDPOINT, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [
                ProviderInfo(
                    name=p.get("name", ""),
                    slug=p.get("slug", ""),
                    status_page_url=p.get("status_page_url"),
                    headquarters=p.get("headquarters"),
                )
                for p in data
            ]
        except Exception as e:
            print(f"[API] providers error: {e}")
            return []

    def get_models(self) -> list:
        try:
            resp = self.session.get(MODELS_ENDPOINT, timeout=30)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            models = []
            for m in data:
                pricing = m.get("pricing", {})
                prompt_price = pricing.get("prompt", "0")
                completion_price = pricing.get("completion", "0")
                try:
                    pp = float(prompt_price)
                except (ValueError, TypeError):
                    pp = 0.0
                try:
                    cp = float(completion_price)
                except (ValueError, TypeError):
                    cp = 0.0
                models.append(ModelInfo(
                    id=m.get("id", ""),
                    name=m.get("name", m.get("id", "")),
                    pricing_prompt=pp,
                    pricing_completion=cp,
                    context_length=m.get("context_length", 0),
                    top_provider=(
                        m.get("top_provider", {}).get("name", "")
                        if isinstance(m.get("top_provider"), dict) else ""
                    ),
                ))
            return models
        except Exception as e:
            print(f"[API] Error fetching models: {e}")
            return []

    def get_model_count(self) -> int:
        try:
            resp = self.session.get(MODELS_COUNT_ENDPOINT, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("count", data.get("data", {}).get("count", 0))
        except Exception as e:
            print(f"[API] Error fetching model count: {e}")
            return 0

    def get_service_status(self) -> ServiceStatus:
        try:
            resp = self.session.get(STATUS_URL, timeout=10)
            text = resp.text.lower()
            status = ServiceStatus()
            if "all systems operational" in text:
                status.overall = "operational"
                status.chat_api = "operational"
                status.data_api = "operational"
                status.homepage = "operational"
            elif "operational" in text:
                status.overall = "degraded"
                status.chat_api = "operational"
                status.data_api = "operational"
                status.homepage = "operational"
            else:
                status.overall = "degraded"
                status.chat_api = "degraded"
                status.data_api = "degraded"
                status.homepage = "degraded"
            return status
        except Exception as e:
            print(f"[API] Error fetching status: {e}")
            return ServiceStatus()


class APIWorker(QObject):
    """Background worker that fetches data and emits signals."""
    key_info_ready = Signal(object)
    models_ready = Signal(object)
    model_count_ready = Signal(int)
    status_ready = Signal(object)
    providers_ready = Signal(object)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.client = APIClient()

    @Slot()
    def fetch_key_info(self):
        try:
            info = self.client.get_key_info()
            if info is None:
                self.error.emit(self.client.last_error or "Unknown error")
            else:
                self.key_info_ready.emit(info)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def fetch_models(self):
        try:
            models = self.client.get_models()
            self.models_ready.emit(models)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def fetch_model_count(self):
        try:
            count = self.client.get_model_count()
            self.model_count_ready.emit(count)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def fetch_status(self):
        try:
            status = self.client.get_service_status()
            self.status_ready.emit(status)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def fetch_providers(self):
        try:
            providers = self.client.get_providers()
            self.providers_ready.emit(providers)
        except Exception as e:
            self.error.emit(str(e))