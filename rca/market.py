"""Answer space of the OpenRCA Market system (cloudbed-1 and cloudbed-2), as listed in OpenRCA's agent prompt."""

SERVICES = (
    "frontend",
    "shippingservice",
    "checkoutservice",
    "currencyservice",
    "adservice",
    "emailservice",
    "cartservice",
    "productcatalogservice",
    "recommendationservice",
    "paymentservice",
)

COMPONENTS = (
    *(f"node-{i}" for i in range(1, 7)),  # nodes
    *(pod for s in SERVICES for pod in (f"{s}-0", f"{s}-1", f"{s}-2", f"{s}2-0")),  # pods
    *SERVICES,  # services: a fault on every pod of the service
)

REASONS = (
    "container CPU load",
    "container memory load",
    "container network packet retransmission",
    "container network packet corruption",
    "container network latency",
    "container packet loss",
    "container process termination",
    "container read I/O load",
    "container write I/O load",
    "node CPU load",
    "node CPU spike",
    "node memory consumption",
    "node disk read I/O consumption",
    "node disk write I/O consumption",
    "node disk space consumption",
)
