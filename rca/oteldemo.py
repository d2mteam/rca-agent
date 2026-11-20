"""Answer space of a self-run OpenTelemetry Demo (Astronomy Shop) with faults injected through its flagd flags.

Reasons name what the demo's failure flags do (adHighCpu, emailMemoryLeak, paymentUnreachable,
productCatalogLockContention, adManualGc, cartFailure/paymentFailure/adFailure, imageSlowLoad, recommendationCacheFailure),
whether or not a run injects them.
"""

COMPONENTS = (
    "frontend-proxy",
    "frontend",
    "ad",
    "cart",
    "checkout",
    "currency",
    "email",
    "image-provider",
    "payment",
    "product-catalog",
    "quote",
    "recommendation",
    "shipping",
    "valkey-cart",
    "astronomy-db",
)

REASONS = (
    "high CPU usage",
    "memory leak",
    "garbage collection pauses",
    "request failures",
    "service unreachable",
    "database lock contention",
    "slow responses",
    "cache failure",
)
