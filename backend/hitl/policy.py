import io
import json
import re
import subprocess
from pathlib import Path

from ruamel.yaml import YAML

from .store import digest


def load_yaml(text):
    value = YAML(typ="safe").load(text)
    if not isinstance(value, dict) or not isinstance(value.get("services"), dict) or not value["services"]:
        raise ValueError("Compose cần có services.")
    return value


def dump_yaml(value):
    stream = io.StringIO()
    YAML().dump(value, stream)
    return stream.getvalue()


def validate_request(project, request, compose_yaml):
    if not re.fullmatch(r"hitl-[a-z0-9][a-z0-9-]{0,39}", project):
        raise ValueError("Project phải có tiền tố hitl- và chỉ chứa chữ thường, số, dấu -.")
    if not request and not compose_yaml:
        raise ValueError("Nhập yêu cầu hoặc nội dung Compose.")
    if len(request or "") > 12000 or len(compose_yaml or "") > 100000:
        raise ValueError("Yêu cầu hoặc Compose quá dài.")


def normalize(text, directory, project):
    document = load_yaml(text)
    unsupported = set(document) - {"name", "version", "services", "networks", "volumes"}
    if unsupported:
        raise ValueError(f"Compose top-level chưa hỗ trợ: {', '.join(sorted(unsupported))}.")
    allowed = {
        "image",
        "ports",
        "volumes",
        "networks",
        "depends_on",
        "environment",
        "healthcheck",
        "mem_limit",
        "deploy",
        "restart",
        "init",
        "labels",
    }
    for name, service in document["services"].items():
        if not isinstance(service, dict):
            raise ValueError(f"Service {name} phải là một object.")
        if unsupported := set(service) - allowed:
            raise ValueError(f"{name}: cấu hình chưa hỗ trợ: {', '.join(sorted(unsupported))}.")
        if set(service.get("deploy", {})) - {"replicas"}:
            raise ValueError("Demo chỉ hỗ trợ deploy.replicas; dùng mem_limit để giới hạn RAM.")
        if "${" in text:
            raise ValueError("Demo yêu cầu cấu hình tường minh; chưa hỗ trợ biến môi trường nội suy.")
        networks = service.get("networks", {})
        if isinstance(networks, dict) and any(v for v in networks.values()):
            raise ValueError("Demo chưa hỗ trợ aliases/static IP trên service network.")
    for group in ("networks", "volumes"):
        for value in document.get(group, {}).values():
            if value and set(value) - {"name", "internal", "driver", "external"}:
                raise ValueError(f"Tùy chọn {group} nằm ngoài phạm vi InfrastructureSpec.")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(directory),
            "-p",
            project,
            "-f",
            "-",
            "config",
            "--format",
            "json",
        ],
        input=text,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise ValueError(f"Compose không hợp lệ: {result.stderr.strip()}")
    return json.loads(result.stdout)


def validate_model(model, project_dir):
    services = model["services"]
    if len(services) > 8:
        raise ValueError("Demo hỗ trợ tối đa 8 services.")
    for name, service in services.items():
        forbidden = {
            "build",
            "privileged",
            "network_mode",
            "pid",
            "ipc",
            "devices",
            "cap_add",
            "container_name",
            "extends",
            "volumes_from",
            "post_start",
            "pre_stop",
            "provider",
            "use_api_socket",
        }
        for key in forbidden:
            if service.get(key):
                raise ValueError(f"{name}: {key} không được hỗ trợ trong demo.")
        if not service.get("image"):
            raise ValueError(f"{name}: cần image có sẵn.")
        replicas = service.get("deploy", {}).get("replicas", 1)
        if not isinstance(replicas, int) or not 1 <= replicas <= 32:
            raise ValueError(f"{name}: replicas phải từ 1 đến 32.")
        if service.get("deploy", {}).get("mode", "replicated") != "replicated":
            raise ValueError("Chỉ hỗ trợ Compose replicated services.")
        for port in service.get("ports", []):
            if port.get("host_ip") != "127.0.0.1" or port.get("protocol", "tcp") != "tcp":
                raise ValueError(f"{name}: demo chỉ publish TCP trên 127.0.0.1.")
            published = str(port.get("published", ""))
            if not published.isdigit() or not 1024 <= int(published) <= 65535:
                raise ValueError(f"{name}: dùng port cố định từ 1024 đến 65535.")
        for volume in service.get("volumes", []):
            if volume["type"] == "bind":
                path = Path(volume["source"]).resolve()
                if (
                    not path.is_relative_to(project_dir.resolve())
                    or not path.is_file()
                    or not volume.get("read_only")
                ):
                    raise ValueError(f"{name}: bind mount phải là file chỉ đọc bên trong project.")
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError("File cấu hình bind mount vượt quá 1 MiB.")
            elif volume["type"] != "volume":
                raise ValueError("Chỉ hỗ trợ named volume và file bind chỉ đọc.")
        if service.get("configs") or service.get("secrets"):
            raise ValueError("Demo dùng file bind chỉ đọc; configs/secrets chưa được hỗ trợ.")
    for group in ("volumes", "networks"):
        for name, item in model.get(group, {}).items():
            if item.get("external") or item.get("driver_opts"):
                raise ValueError(f"{group}/{name}: không dùng tài nguyên external hoặc driver_opts.")
            expected_name = f"{model['name']}_{name}"
            if item.get("name", expected_name) != expected_name:
                raise ValueError(f"{group}/{name}: tên tài nguyên phải nằm trong project.")
            if item.get("driver") and item["driver"] not in ("bridge", "local"):
                raise ValueError("Demo chỉ dùng bridge network/local volume.")


def source_fingerprints(compose_path, model):
    paths = {str(compose_path.resolve())}
    for service in model["services"].values():
        for volume in service.get("volumes", []):
            if volume["type"] == "bind":
                paths.add(str(Path(volume["source"]).resolve()))
    return {p: digest(Path(p).read_bytes()) if Path(p).is_file() else None for p in sorted(paths)}


def check_source_fingerprints(fingerprints):
    return all(
        (digest(Path(p).read_bytes()) if Path(p).is_file() else None) == expected
        for p, expected in fingerprints.items()
    )
