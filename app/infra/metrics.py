from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Iterator

try:
    from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
    _PROM_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback for environments without dependency
    _PROM_AVAILABLE = False

    class CollectorRegistry:  # type: ignore
        def __init__(self, *args, **kwargs) -> None:
            pass

    class Counter:  # type: ignore
        def __init__(self, *args, **kwargs) -> None:
            pass

        def labels(self, **kwargs) -> "Counter":
            return self

        def inc(self, amount: float = 1.0) -> None:
            pass

        @property
        def samples(self):  # type: ignore
            return []

    class Histogram:  # type: ignore
        def __init__(self, *args, **kwargs) -> None:
            pass

        def observe(self, value: float) -> None:
            pass

        def time(self, func):  # pragma: no cover - unused
            return func

        @property
        def samples(self):  # type: ignore
            return []

    def generate_latest(registry: CollectorRegistry) -> bytes:  # type: ignore
        return b""

from .settings import Settings, get_settings


@dataclass(slots=True)
class MetricHandles:
    requests_total: Counter | None = None
    errors_total: Counter | None = None
    latency_histogram: Histogram | None = None
    tokens_in_total: Counter | None = None
    tokens_out_total: Counter | None = None


class Metrics:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._enabled = settings.metrics_enabled
        self._registry = CollectorRegistry(auto_describe=True)
        self._handles = MetricHandles()
        if self._enabled:
            self._init_metrics()

    def _init_metrics(self) -> None:
        self._handles.requests_total = Counter(
            "requests_total",
            "Total number of processed requests",
            labelnames=("source", "type"),
            registry=self._registry,
        )
        self._handles.errors_total = Counter(
            "errors_total",
            "Total number of errors by stage",
            labelnames=("stage",),
            registry=self._registry,
        )
        self._handles.latency_histogram = Histogram(
            "latency_ms",
            "Request latency in milliseconds",
            registry=self._registry,
            unit="milliseconds",
            buckets=(50, 100, 200, 400, 800, 1500, 3000, 5000, 10000),
        )
        self._handles.tokens_in_total = Counter(
            "gigachat_tokens_in_total",
            "Total number of tokens sent to GigaChat",
            registry=self._registry,
        )
        self._handles.tokens_out_total = Counter(
            "gigachat_tokens_out_total",
            "Total number of tokens received from GigaChat",
            registry=self._registry,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry

    def inc_request(self, source: str, kind: str) -> None:
        if self._handles.requests_total:
            self._handles.requests_total.labels(source=source, type=kind).inc()

    def inc_error(self, stage: str) -> None:
        if self._handles.errors_total:
            self._handles.errors_total.labels(stage=stage).inc()

    def observe_latency_ms(self, milliseconds: float) -> None:
        if self._handles.latency_histogram:
            self._handles.latency_histogram.observe(milliseconds)

    def add_tokens(self, tokens_in: int | None = None, tokens_out: int | None = None) -> None:
        if tokens_in and self._handles.tokens_in_total:
            self._handles.tokens_in_total.inc(tokens_in)
        if tokens_out and self._handles.tokens_out_total:
            self._handles.tokens_out_total.inc(tokens_out)

    @contextmanager
    def latency_timer(self) -> Iterator[None]:
        start = perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (perf_counter() - start) * 1000
            self.observe_latency_ms(elapsed_ms)

    def export(self) -> bytes:
        return generate_latest(self._registry)


_metrics: Metrics | None = None


def get_metrics(settings: Settings | None = None) -> Metrics:
    global _metrics
    if _metrics:
        return _metrics
    settings = settings or get_settings()
    _metrics = Metrics(settings)
    return _metrics
