"""The only executable desired-state representation. Compose is an import/export format."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .governance import GatewayPolicy
from .store import digest

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,62}$")]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PortSpec(DomainModel):
    target: int = Field(ge=1, le=65535)
    published: int = Field(ge=1024, le=65535)
    host_ip: Literal["127.0.0.1"] = "127.0.0.1"
    protocol: Literal["tcp"] = "tcp"


class MountSpec(DomainModel):
    type: Literal["bind", "volume"]
    source: str
    target: str
    read_only: bool = False
    selinux: str | None = None


class DependencySpec(DomainModel):
    condition: Literal["service_started", "service_healthy"] = "service_started"
    required: bool = True


class HealthSpec(DomainModel):
    test: list[str]
    interval: str | None = None
    timeout: str | None = None
    start_period: str | None = None
    start_interval: str | None = None
    retries: int | None = Field(default=None, ge=1)


class ServiceSpec(DomainModel):
    image: str = Field(min_length=1)
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    replicas: int = Field(ge=1, le=32)
    memory_bytes: int = Field(ge=0)
    ports: list[PortSpec] = Field(default_factory=list)
    mounts: list[MountSpec] = Field(default_factory=list)
    dependencies: dict[Name, DependencySpec] = Field(default_factory=dict)
    networks: list[Name]
    environment: dict[str, str] = Field(default_factory=dict)
    healthcheck: HealthSpec | None = None
    restart: Literal["no", "always", "on-failure", "unless-stopped"] = "no"
    init: bool | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class NetworkSpec(DomainModel):
    name: str
    internal: bool = False


class VolumeSpec(DomainModel):
    name: str


class InfrastructureSpec(DomainModel):
    schema_version: Literal[1] = 1
    policy_snapshot: GatewayPolicy = Field(default_factory=GatewayPolicy)
    project: str = Field(pattern=r"^hitl-[a-z0-9][a-z0-9-]{0,39}$")
    services: dict[Name, ServiceSpec]
    networks: dict[Name, NetworkSpec]
    volumes: dict[Name, VolumeSpec] = Field(default_factory=dict)
    probe_url: str | None = None

    @model_validator(mode="after")
    def semantics(self):
        if not self.services or len(self.services) > 8:
            raise ValueError("Spec cần từ 1 đến 8 services.")
        bindings = set()
        for name, service in self.services.items():
            if service.replicas > self.policy_snapshot.max_replicas:
                raise ValueError(
                    f"{name}: replicas vượt giới hạn {self.policy_snapshot.max_replicas} của policy."
                )
            if (
                service.memory_bytes == 0
                or service.memory_bytes > self.policy_snapshot.max_memory_mib * 1024 * 1024
            ):
                raise ValueError(f"{name}: RAM vượt giới hạn policy.")
            if (
                service.ports
                and self.policy_snapshot.block_sensitive_services
                and any(
                    word in service.image.lower().split("/")[-1].split(":")[0]
                    for word in ("postgres", "redis", "mysql", "mariadb", "mongo", "elastic", "memcache")
                )
            ):
                raise ValueError(f"{name}: policy chặn publish dịch vụ nhạy cảm.")
            if not set(service.dependencies) <= self.services.keys():
                raise ValueError(f"{name}: dependency trỏ tới service không tồn tại.")
            if not set(service.networks) <= self.networks.keys():
                raise ValueError(f"{name}: network không tồn tại.")
            for dep, rule in service.dependencies.items():
                if rule.condition == "service_healthy" and not self.services[dep].healthcheck:
                    raise ValueError(f"{name}: {dep} cần healthcheck để chờ service_healthy.")
            if service.replicas > 1 and service.ports:
                raise ValueError(f"{name}: không thể chia sẻ một host port cố định cho nhiều replicas.")
            for port in service.ports:
                if (
                    port.target in self.policy_snapshot.blocked_ports
                    or port.published in self.policy_snapshot.blocked_ports
                ):
                    raise ValueError("Policy chặn port nhạy cảm.")
                key = (port.host_ip, port.published, port.protocol)
                if key in bindings:
                    raise ValueError(f"Port {port.published} bị trùng giữa services.")
                bindings.add(key)
            destinations = set()
            for mount in service.mounts:
                if mount.target in destinations:
                    raise ValueError(f"{name}: mount target bị trùng.")
                destinations.add(mount.target)
                if mount.type == "volume" and mount.source not in self.volumes:
                    raise ValueError(f"{name}: volume {mount.source} không tồn tại.")
                if mount.type == "bind" and not mount.read_only:
                    raise ValueError("Bind mount phải chỉ đọc.")
            if any(label.startswith("com.docker.compose.") for label in service.labels):
                raise ValueError("Không được ghi đè ownership labels của Docker Compose.")
        visited, stack = set(), set()

        def visit(name):
            if name in stack:
                raise ValueError("Dependencies có vòng lặp.")
            if name in visited:
                return
            stack.add(name)
            for dependency in self.services[name].dependencies:
                visit(dependency)
            stack.remove(name)
            visited.add(name)

        for name in self.services:
            visit(name)
        for name, network in self.networks.items():
            if network.name != f"{self.project}_{name}":
                raise ValueError("Network phải thuộc project.")
        for name, volume in self.volumes.items():
            if volume.name != f"{self.project}_{name}":
                raise ValueError("Volume phải thuộc project.")
        return self

    @classmethod
    def from_compose(cls, model, image_ids, policy=None):
        services = {}
        for name, service in model["services"].items():
            services[name] = ServiceSpec(
                image=service["image"],
                image_id=image_ids[name],
                replicas=service.get("deploy", {}).get("replicas", 1),
                memory_bytes=service.get("mem_limit", 0),
                ports=[
                    PortSpec(
                        target=p["target"],
                        published=int(p["published"]),
                        host_ip=p.get("host_ip", "127.0.0.1"),
                        protocol=p.get("protocol", "tcp"),
                    )
                    for p in service.get("ports", [])
                ],
                mounts=[
                    MountSpec(
                        type=v["type"],
                        source=v["source"],
                        target=v["target"],
                        read_only=v.get("read_only", False),
                        selinux=v.get("bind", {}).get("selinux"),
                    )
                    for v in service.get("volumes", [])
                ],
                dependencies={
                    n: DependencySpec(
                        condition=d.get("condition", "service_started"), required=d.get("required", True)
                    )
                    for n, d in service.get("depends_on", {}).items()
                },
                networks=list(service.get("networks", {})),
                environment={k: str(v) for k, v in service.get("environment", {}).items()},
                healthcheck=HealthSpec.model_validate(service["healthcheck"])
                if service.get("healthcheck")
                else None,
                restart=service.get("restart", "no"),
                init=service.get("init"),
                labels=service.get("labels", {}),
            )
        ports = services.get("nginx").ports if "nginx" in services else []
        return cls(
            project=model["name"],
            policy_snapshot=policy or GatewayPolicy(),
            services=services,
            networks={
                n: NetworkSpec(name=v["name"], internal=v.get("internal", False))
                for n, v in model.get("networks", {}).items()
            },
            volumes={n: VolumeSpec(name=v["name"]) for n, v in model.get("volumes", {}).items()},
            probe_url=f"http://127.0.0.1:{ports[0].published}"
            if ports and {"backend", "db"} <= services.keys()
            else None,
        )

    def to_compose(self):
        services = {}
        for name, service in self.services.items():
            entry = {
                "image": service.image_id,
                "pull_policy": "never",
                "deploy": {"replicas": service.replicas},
                "mem_limit": service.memory_bytes,
                "networks": service.networks,
                "restart": service.restart,
            }
            if service.environment:
                entry["environment"] = service.environment
            if service.ports:
                entry["ports"] = [{**p.model_dump(), "published": str(p.published)} for p in service.ports]
            if service.dependencies:
                entry["depends_on"] = {n: d.model_dump() for n, d in service.dependencies.items()}
            if service.healthcheck:
                entry["healthcheck"] = service.healthcheck.model_dump(exclude_none=True)
            if service.init is not None:
                entry["init"] = service.init
            if service.labels:
                entry["labels"] = service.labels
            if service.mounts:
                entry["volumes"] = []
                for m in service.mounts:
                    mount = m.model_dump(exclude={"selinux"})
                    if m.type == "bind":
                        mount["bind"] = {
                            "create_host_path": False,
                            **({"selinux": m.selinux} if m.selinux else {}),
                        }
                    entry["volumes"].append(mount)
            services[name] = entry
        return {
            "name": self.project,
            "services": services,
            "networks": {n: v.model_dump() for n, v in self.networks.items()},
            "volumes": {n: v.model_dump() for n, v in self.volumes.items()},
        }


def plan_hash(plan):
    payload = {
        key: plan[key]
        for key in (
            "project",
            "infrastructure_spec",
            "artifact_hash",
            "artifact_fingerprints",
            "source_fingerprints",
            "runtime_hash",
            "record_baseline",
        )
    }
    if "policy_revision" in plan:
        payload["policy_revision"] = plan["policy_revision"]
    if "baseline_status" in plan:
        payload["baseline_status"] = plan["baseline_status"]
    return digest(payload)
