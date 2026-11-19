"""The OpenRCA systems this package can investigate: answer space and normal day of each."""

from dataclasses import dataclass
from datetime import date

from rca import bank, telecom


@dataclass(frozen=True)
class System:
    name: str
    components: tuple[str, ...]
    reasons: tuple[str, ...]
    normal_day: date | None  # a day without queries, the fallback reference; None if there is none

    def check(self, component: str, reason: str) -> None:
        if component not in self.components:
            raise SystemExit(f"unknown component {component!r}; choose from {', '.join(self.components)}")
        if reason not in self.reasons:
            raise SystemExit(f"unknown reason {reason!r}; choose from {'; '.join(self.reasons)}")


SYSTEMS = {
    "Bank": System("Bank", bank.COMPONENTS, bank.REASONS, date(2021, 3, 5)),
    "Telecom": System("Telecom", telecom.COMPONENTS, telecom.REASONS, None),
}


def get(name: str) -> System:
    if name not in SYSTEMS:
        raise SystemExit(f"unknown system {name!r}; choose from {', '.join(SYSTEMS)}")
    return SYSTEMS[name]
