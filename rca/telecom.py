"""Answer space of the OpenRCA Telecom system, as listed in OpenRCA's agent prompt."""

COMPONENTS = (
    *(f"os_{i:03d}" for i in range(1, 23)),  # nodes
    *(f"docker_{i:03d}" for i in range(1, 9)),  # pods
    *(f"db_{i:03d}" for i in range(1, 14)),  # services
)

REASONS = (
    "CPU fault",
    "network delay",
    "network loss",
    "db connection limit",
    "db close",
)
