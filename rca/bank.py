"""Answer space of the OpenRCA Bank system, as listed in OpenRCA's agent prompt."""

COMPONENTS = (
    "apache01",
    "apache02",
    "Tomcat01",
    "Tomcat02",
    "Tomcat03",
    "Tomcat04",
    "MG01",
    "MG02",
    "IG01",
    "IG02",
    "Mysql01",
    "Mysql02",
    "Redis01",
    "Redis02",
)

REASONS = (
    "high CPU usage",
    "high memory usage",
    "network latency",
    "network packet loss",
    "high disk I/O read usage",
    "high disk space usage",
    "high JVM CPU load",
    "JVM Out of Memory (OOM) Heap",
)
