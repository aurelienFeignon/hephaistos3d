from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def config():
    return yaml.safe_load((ROOT / "compose.yaml").read_text())


def test_default_services_have_no_gpu_or_host_ports():
    services = config()["services"]
    assert {"migrate", "api", "janitor", "worker"} <= services.keys()
    defaults = {name: service for name, service in services.items() if not service.get("profiles")}
    assert set(defaults) == {"migrate", "api", "janitor"}
    assert all(not s.get("ports") and not s.get("devices") and "deploy" not in s for s in defaults.values())
    assert services["worker"]["profiles"] == ["gpu"]
    assert services["worker"]["deploy"]["resources"]["reservations"]["devices"][0]["capabilities"] == ["gpu"]


def test_runtime_services_wait_for_migration_and_have_healthchecks():
    services = config()["services"]
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert services["migrate"]["restart"] == "no"
    for name in ("api", "worker", "janitor"):
        service = services[name]
        assert service["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
        assert service["healthcheck"]["test"] == ["CMD", "python", "-m", "hephaistos.healthcheck", name]
        assert service["restart"] == "unless-stopped"


def test_containers_are_nonroot_and_have_readonly_roots():
    for name, service in config()["services"].items():
        assert service["read_only"] is True, name
        assert service["user"] != "root"
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert "/var/run/docker.sock" not in str(service.get("volumes"))


def test_worker_and_janitor_are_offline_and_weights_are_readonly():
    services = config()["services"]
    assert services["worker"]["network_mode"] == "none"
    assert services["janitor"]["network_mode"] == "none"
    assert services["migrate"]["network_mode"] == "none"
    mounts = {v["target"]: v for v in services["worker"]["volumes"]}
    assert mounts["/models"]["read_only"] is True
    assert mounts["/data/sources"]["read_only"] is True
    assert mounts["/cache"]["read_only"] is False
    assert all("/models" not in str(v) for v in services["api"]["volumes"])
    assert config()["networks"]["backend"]["internal"] is True


def test_bind_mounts_never_create_root_owned_directories():
    for name in ("migrate", "api", "worker", "janitor"):
        for mount in config()["services"][name]["volumes"]:
            assert mount["type"] == "bind"
            assert mount["bind"]["create_host_path"] is False
