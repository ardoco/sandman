"""Plan-approval metrics (T6.4). No-op without an OTLP endpoint, like the other telemetry."""

from collections.abc import Callable, Iterable

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from sandman_agent.telemetry import ATTR_OUTCOME

METRIC_AWAITING = "sandman.orchestrator.plans.awaiting_approval"
METRIC_OLDEST_AGE = "sandman.orchestrator.plans.oldest_waiting_age"
METRIC_OUTCOMES = "sandman.orchestrator.plans.outcomes"

_meter = metrics.get_meter("sandman_orchestrator")
_outcomes = _meter.create_counter(METRIC_OUTCOMES, description="Plans decided or expired")


def plan_outcome(status: str) -> None:
    """status: APPROVED, REJECTED or EXPIRED."""
    _outcomes.add(1, {ATTR_OUTCOME: status.lower()})


def observe(awaiting: Callable[[], tuple[int, float]]) -> None:
    """Register the gauges; awaiting() returns (plans waiting, age in seconds of the oldest)."""

    def count(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(awaiting()[0])]

    def age(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(awaiting()[1])]

    _meter.create_observable_gauge(METRIC_AWAITING, [count], unit="{plan}", description="Plans waiting for approval")
    _meter.create_observable_gauge(
        METRIC_OLDEST_AGE, [age], unit="s", description="Age of the oldest plan waiting for approval"
    )
