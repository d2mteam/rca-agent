import pytest

from rca.statistical import reason_for


@pytest.mark.parametrize(
    "kpi, reason",
    [
        ("JVM-Memory_7779_JVM_Memory_HeapMemoryUsed", "JVM Out of Memory (OOM) Heap"),
        ("JVM-Memory_7779_JVM_Memory_NoHeapMemoryUsed", None),
        ("JVM-Operating System_7779_JVM_JVM_CPULoad", "high JVM CPU load"),
        ("OSLinux-CPU_CPU-0_SingleCpuidle", "high CPU usage"),
        ("OSLinux-CPU_CPU_CPUWio", None),
        ("OSLinux-OSLinux_MEMORY_MEMORY_MEMTotalMem", None),
        ("OSLinux-OSLinux_LOCALDISK_LOCALDISK-sdb_DSKRead", "high disk I/O read usage"),
        ("OSLinux-OSLinux_FILESYSTEM_-tomcat_FSCapacity", "high disk space usage"),
        ("OSLinux-OSLinux_NETWORK_NETWORK_TCP-FIN-WAIT", "network packet loss"),
        ("OSLinux-OSLinux_NETWORK_ens160_NETKBTotalPerSec", "network latency"),
        ("Mysql-MySQL_3306_Handler Rollback", None),
    ],
)
def test_reason_for_kpi(kpi, reason):
    assert reason_for(kpi) == reason
