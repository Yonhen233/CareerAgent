import time
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class AppTelemetry:
    started_at: float = field(default_factory=time.time)
    request_count: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    route_counts: Counter[str] = field(default_factory=Counter)
    total_latency_ms: float = 0.0

    def record(self, *, path: str, status_code: int, latency_ms: float) -> None:
        self.request_count += 1
        self.status_counts[str(status_code)] += 1
        self.route_counts[path] += 1
        self.total_latency_ms += latency_ms

    def snapshot(self) -> dict:
        uptime_seconds = max(time.time() - self.started_at, 0.0)
        avg_latency = self.total_latency_ms / self.request_count if self.request_count else 0.0
        return {
            "uptime_seconds": round(uptime_seconds, 2),
            "request_count": self.request_count,
            "avg_latency_ms": round(avg_latency, 2),
            "status_counts": dict(self.status_counts),
            "top_routes": dict(self.route_counts.most_common(10)),
        }


telemetry = AppTelemetry()
