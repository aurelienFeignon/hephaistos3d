from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def config():
    return yaml.safe_load((ROOT / "compose.yaml").read_text())


def test_default_services_have_no_gpu_and_only_the_frontend_is_published():
    services = config()["services"]
    assert {"migrate", "api", "janitor", "frontend", "worker"} <= services.keys()
    defaults = {name: service for name, service in services.items() if not service.get("profiles")}
    assert set(defaults) == {"migrate", "api", "janitor", "frontend"}
    assert all(not s.get("devices") and "deploy" not in s for s in defaults.values())
    assert {name for name, service in services.items() if service.get("ports")} == {"frontend"}
    assert services["frontend"]["ports"] == ["${HEPHAISTOS_BIND_ADDR:-127.0.0.1}:${HEPHAISTOS_PORT:-3030}:3030"]
    assert "ports" not in services["api"]
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


def test_frontend_reaches_the_api_only_through_the_internal_network():
    compose = config()
    services = compose["services"]
    assert services["api"]["networks"] == ["backend"]
    assert set(services["frontend"]["networks"]) == {"backend", "edge"}
    assert compose["networks"]["edge"].get("internal") is not True
    assert services["frontend"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["frontend"]["environment"]["HEPHAISTOS_API_URL"] == "http://api:8000"


def test_frontend_has_no_data_access_and_only_its_own_settings():
    frontend = config()["services"]["frontend"]
    assert "volumes" not in frontend
    assert set(frontend["environment"]) == {
        "HOSTNAME",
        "PORT",
        "HEPHAISTOS_API_URL",
        "API_ACCESS_TOKEN",
        "FRONTEND_ALLOWED_ORIGINS",
        "API_JSON_MAX_BYTES",
        "UPLOAD_MAX_BYTES",
        "API_REQUEST_TIMEOUT_SECONDS",
    }
    assert frontend["restart"] == "unless-stopped"
    assert frontend["healthcheck"]["test"][0] == "CMD"
    assert frontend["mem_limit"]


def test_build_context_never_includes_secrets_or_local_frontend_artifacts():
    allowed = [line for line in (ROOT / ".dockerignore").read_text().splitlines() if line.startswith("!")]
    assert (ROOT / ".dockerignore").read_text().splitlines()[1] == "**"
    assert "!frontend/" not in allowed  # réinclurait node_modules, .next et tout fichier local
    assert not any(".env" in line or "node_modules" in line or ".next" in line for line in allowed)
    assert "!frontend/package-lock.json" in allowed
