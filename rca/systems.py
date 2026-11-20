"""The OpenRCA systems this package can investigate: answer space and normal day of each."""

from dataclasses import dataclass
from datetime import date

from rca import bank, market, oteldemo, telecom


@dataclass(frozen=True)
class System:
    name: str
    components: tuple[str, ...]
    reasons: tuple[str, ...]
    normal_day: date | None  # a day without queries, the fallback reference; None if there is none
    directory: str = ""  # under data/openrca/ and data/parquet/; default: the name
    prefix: str = ""  # of its case ids, e.g. bank-001; default: the name in lower case

    @property
    def path(self) -> str:
        return self.directory or self.name

    @property
    def case_prefix(self) -> str:
        return self.prefix or self.name.lower()

    def check(self, component: str, reason: str) -> None:
        if component not in self.components:
            raise SystemExit(f"unknown component {component!r}; choose from {', '.join(self.components)}")
        if reason not in self.reasons:
            raise SystemExit(f"unknown reason {reason!r}; choose from {'; '.join(self.reasons)}")


SYSTEMS = {
    "Bank": System("Bank", bank.COMPONENTS, bank.REASONS, date(2021, 3, 5)),
    "Telecom": System("Telecom", telecom.COMPONENTS, telecom.REASONS, None),
    # Market's two service groups were collected separately, each with its own queries.
    "Market1": System("Market1", market.COMPONENTS, market.REASONS, None, "Market/cloudbed-1", "market1"),
    "Market2": System("Market2", market.COMPONENTS, market.REASONS, None, "Market/cloudbed-2", "market2"),
    # Self-run OpenTelemetry Demo, faults injected through flagd; converted to this layout by a script.
    "OtelDemo": System("OtelDemo", oteldemo.COMPONENTS, oteldemo.REASONS, None),
}


def get(name: str) -> System:
    if name not in SYSTEMS:
        raise SystemExit(f"unknown system {name!r}; choose from {', '.join(SYSTEMS)}")
    return SYSTEMS[name]
