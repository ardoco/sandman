"""corenlp-gateway (T2.5): path allowlist, header replacement, fixed upstream. Needs Docker; skipped without it."""

import base64
import http.client
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from testcontainers.core.container import DockerContainer

DEPLOY = Path(__file__).parents[2] / "deploy"
COMPOSE = DEPLOY / "docker-compose.yml"
CREDENTIALS = "kit-user:p@ss:w0rd\"'$"
BASIC = "Basic " + base64.b64encode(CREDENTIALS.encode()).decode()
TOKENS = {"tlr-agent": "t" * 48, "inconsistency-agent": "i" * 48}


def caller(name: str, token: str | None = None) -> dict[str, str]:
    value = base64.b64encode(f"{name}:{token or TOKENS[name]}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


TLR = caller("tlr-agent")


class Upstream(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, str, str]] = []

    def log_message(self, *args):
        pass

    def handle_any(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        type(self).seen.append((self.command, self.path, self.headers.get("Authorization", ""), self.headers["Host"]))
        body = b"upstream"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = handle_any


def compose_service(name: str) -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"][name]


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("corenlp")
    server = ThreadingHTTPServer(("0.0.0.0", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    credentials = tmp / "corenlp_credentials"
    credentials.write_text(CREDENTIALS + "\n")
    credentials.chmod(0o644)
    token_mounts = {}
    for name, token in TOKENS.items():
        f = tmp / f"token_{name}"
        f.write_text(token)
        f.chmod(0o644)
        token_mounts[str(f)] = f"/run/secrets/corenlp_client_token_{name}"
    container = (
        DockerContainer(compose_service("corenlp-gateway")["image"])
        .with_kwargs(entrypoint=["/bin/sh", "/entrypoint.sh"], extra_hosts={"host.docker.internal": "host-gateway"})
        .with_env("SANDMAN_CORENLP_MODE", "remote")
        .with_env("SANDMAN_CORENLP_URL", f"http://host.docker.internal:{server.server_port}")
        .with_env("XDG_CONFIG_HOME", "/tmp")
        .with_env("XDG_DATA_HOME", "/tmp")
        .with_volume_mapping(str(DEPLOY / "corenlp-gateway" / "Caddyfile"), "/etc/caddy/Caddyfile", "ro")
        .with_volume_mapping(str(DEPLOY / "corenlp-gateway" / "entrypoint.sh"), "/entrypoint.sh", "ro")
        .with_volume_mapping(str(credentials), "/run/secrets/corenlp_credentials", "ro")
        .with_exposed_ports(8080)
    )
    for source, target in token_mounts.items():
        container.with_volume_mapping(source, target, "ro")
    try:
        container.start()
    except Exception as e:
        server.shutdown()
        pytest.skip(f"docker unavailable: {e}")
    host, port = container.get_container_host_ip(), int(container.get_exposed_port(8080))
    for _ in range(60):
        try:
            if request(host, port, "GET", "/stanfordnlp/health", TLR)[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    Upstream.seen = []
    yield host, port, server.server_port, container
    container.stop()
    server.shutdown()


def request(host, port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection(host, port, timeout=30)
    conn.putrequest(method, path, skip_host=False)  # path is sent as is: no client-side normalization
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    conn.putheader("Content-Length", str(len(body or b"")))
    conn.endheaders(body or b"")
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


ALLOWED = [("POST", "/stanfordnlp"), ("GET", "/stanfordnlp/health"), ("GET", "/stanfordnlp/version")]

BLOCKED = [
    ("GET", "/"),
    ("GET", "/h2"),
    ("GET", "/h2/"),
    ("GET", "/h2/login.jsp"),
    ("POST", "/h2"),
    ("GET", "/stanfordnlp/registration"),
    ("POST", "/stanfordnlp/registration"),
    ("PUT", "/stanfordnlp/registration"),
    ("GET", "/swagger-ui/index.html"),
    ("GET", "/swagger-ui.html"),
    ("GET", "/v3/api-docs"),
    ("GET", "/actuator/health"),
    ("GET", "/stanfordnlp"),
    ("GET", "/stanfordnlp?text=Hi."),
    ("PUT", "/stanfordnlp"),
    ("DELETE", "/stanfordnlp"),
    ("POST", "/stanfordnlp/health"),
    ("POST", "/stanfordnlp/version"),
    ("DELETE", "/stanfordnlp/version"),
    ("GET", "/stanfordnlp/"),
    ("GET", "/stanfordnlp/health/"),
    ("GET", "/stanfordnlp/other"),
    ("GET", "//h2"),
    ("GET", "/stanfordnlp/../h2"),
    ("GET", "/stanfordnlp/health/../registration"),
    ("GET", "/stanfordnlp/health/..%2fregistration"),
    ("GET", "/stanfordnlp/health%2f..%2fregistration"),
    ("GET", "/stanfordnlp/%2e%2e/h2"),
    ("GET", "/stanfordnlp/health;x=1/../registration"),
    ("GET", "/%73wagger-ui/index.html"),
    ("GET", "/stanfordnlp%2fregistration"),
    ("GET", "/stanfordnlp/health%00"),
]


@pytest.mark.parametrize(("method", "path"), BLOCKED)
def test_other_requests_get_404_and_never_reach_the_upstream(gateway, method, path):
    host, port, _, _ = gateway
    before = len(Upstream.seen)
    status, _ = request(host, port, method, path, TLR)
    assert status in (400, 404), (method, path, status)
    status, _ = request(host, port, method, path)
    assert status in (400, 401), (method, path, status)
    assert Upstream.seen[before:] == [], (method, path)


@pytest.mark.parametrize(("method", "path"), ALLOWED)
@pytest.mark.parametrize("name", list(TOKENS))
def test_allowed_requests_carry_only_the_gateway_credentials(gateway, method, path, name):
    host, port, upstream_port, _ = gateway
    before = len(Upstream.seen)
    status, body = request(host, port, method, path, caller(name), b'{"text": "Hi."}' if method == "POST" else None)
    assert (status, body) == (200, b"upstream")
    assert Upstream.seen[before:] == [(method, path, BASIC, f"host.docker.internal:{upstream_port}")]


WRONG_CALLERS = {
    "none": {},
    "wrong token": caller("tlr-agent", "x" * 48),
    "token of the other caller": caller("tlr-agent", TOKENS["inconsistency-agent"]),
    "unknown user": caller("tlr-agent")
    | {"Authorization": caller("plugin-stubs", TOKENS["tlr-agent"])["Authorization"]},
    "empty token": {"Authorization": "Basic " + base64.b64encode(b"tlr-agent:").decode()},
    "bearer token": {"Authorization": "Bearer " + TOKENS["tlr-agent"]},
    "bearer base64": {
        "Authorization": "Bearer " + base64.b64encode(f"tlr-agent:{TOKENS['tlr-agent']}".encode()).decode()
    },
    "upstream credentials": {"Authorization": BASIC},
    "default upstream user": {"Authorization": "Basic YWRtaW46Y2hhbmdlbWU="},
    "garbage": {"Authorization": "Basic !!!"},
}


@pytest.mark.parametrize(("method", "path"), ALLOWED)
@pytest.mark.parametrize("why", list(WRONG_CALLERS))
def test_missing_or_wrong_caller_token_gets_401_and_the_upstream_sees_nothing(gateway, method, path, why):
    host, port, _, _ = gateway
    before = len(Upstream.seen)
    status, _ = request(host, port, method, path, WRONG_CALLERS[why], b"{}" if method == "POST" else None)
    assert status == 401, (why, status)
    assert Upstream.seen[before:] == []


def test_duplicate_authorization_headers_never_authenticate_with_the_wrong_one(gateway):
    host, port, _, _ = gateway
    before = len(Upstream.seen)
    conn = http.client.HTTPConnection(host, port, timeout=30)
    conn.putrequest("GET", "/stanfordnlp/health")
    conn.putheader("Authorization", "Basic YQ==")
    conn.putheader("Authorization", TLR["Authorization"])
    conn.endheaders()
    status = conn.getresponse().status
    seen = Upstream.seen[before:]
    assert status in (200, 401)
    assert [x[2] for x in seen] in ([], [BASIC])


def test_healthcheck_listener_is_loopback_only_and_serves_the_health_path_only(gateway):
    host, port, _, container = gateway
    before = len(Upstream.seen)
    code, out = container.exec(["wget", "-q", "-O", "-", "http://127.0.0.1:8081/stanfordnlp/health"])
    assert (code, out) == (0, b"upstream")
    assert Upstream.seen[before:][0][:3] == ("GET", "/stanfordnlp/health", BASIC)
    for path in ("/stanfordnlp/version", "/h2", "/stanfordnlp/registration"):
        code, _ = container.exec(["wget", "-q", "-O", "-", f"http://127.0.0.1:8081{path}"])
        assert code != 0, path
    assert len(Upstream.seen) == before + 1
    with pytest.raises(OSError):
        request(host, 8081, "GET", "/stanfordnlp/health")


@pytest.fixture(scope="module")
def networks():
    docker = pytest.importorskip("docker")
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        pytest.skip(f"docker unavailable: {e}")
    tag = f"corenlp-test-{int(time.time())}"
    made = [client.networks.create(f"{tag}-{n}", internal=True) for n in ("internal", "corenlp")]
    yield client, made[0], made[1], tag
    for n in made:
        n.remove()


def test_a_container_on_internal_cannot_reach_the_gateway_but_a_caller_on_corenlp_can(networks, tmp_path):
    client, internal, corenlp, tag = networks
    image = compose_service("corenlp-gateway")["image"]
    for name, token in TOKENS.items():
        (tmp_path / f"t_{name}").write_text(token)
        (tmp_path / f"t_{name}").chmod(0o644)
    (tmp_path / "cred").write_text("kit:secret\n")
    (tmp_path / "cred").chmod(0o644)
    volumes = {
        str(DEPLOY / "corenlp-gateway" / "Caddyfile"): {"bind": "/etc/caddy/Caddyfile", "mode": "ro"},
        str(DEPLOY / "corenlp-gateway" / "entrypoint.sh"): {"bind": "/entrypoint.sh", "mode": "ro"},
        str(tmp_path / "cred"): {"bind": "/run/secrets/corenlp_credentials", "mode": "ro"},
    } | {str(tmp_path / f"t_{n}"): {"bind": f"/run/secrets/corenlp_client_token_{n}", "mode": "ro"} for n in TOKENS}
    gateway = client.containers.run(
        image,
        entrypoint=["/bin/sh", "/entrypoint.sh"],
        name=f"{tag}-corenlp-gateway",
        network=corenlp.name,
        environment={
            "SANDMAN_CORENLP_URL": "http://nowhere.invalid",
            "XDG_CONFIG_HOME": "/tmp",
            "XDG_DATA_HOME": "/tmp",
        },
        volumes=volumes,
        detach=True,
    )
    try:
        time.sleep(3)
        outsider = client.containers.run(
            "curlimages/curl:8.10.1", "sleep 60", entrypoint="", network=internal.name, detach=True
        )
        caller_ = client.containers.run(
            "curlimages/curl:8.10.1", "sleep 60", entrypoint="", network=corenlp.name, detach=True
        )
        try:
            gw = gateway.name
            code, out = outsider.exec_run(["curl", "-sS", "-m", "5", "-o", "/dev/null", f"http://{gw}:8080/"])
            assert code != 0, out
            ip = gateway.attrs["NetworkSettings"]["Networks"][corenlp.name]["IPAddress"]
            code, out = outsider.exec_run(["curl", "-sS", "-m", "5", "-o", "/dev/null", f"http://{ip}:8080/"])
            assert code != 0, out
            code, out = caller_.exec_run(
                [
                    "curl",
                    "-sS",
                    "-m",
                    "5",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    f"http://{gw}:8080/stanfordnlp/health",
                ]
            )
            assert out == b"401", out
            gateway.reload()
            assert list(gateway.attrs["NetworkSettings"]["Networks"]) == [corenlp.name]
        finally:
            outsider.remove(force=True)
            caller_.remove(force=True)
    finally:
        gateway.remove(force=True)


def test_credentials_are_in_no_compose_file_or_container_setting():
    compose = yaml.safe_load(COMPOSE.read_text())
    assert "corenlp_credentials" in compose["secrets"]
    users = [n for n, s in compose["services"].items() if "corenlp_credentials" in s.get("secrets", [])]
    assert users == ["corenlp-gateway"]
    local = [n for n, s in compose["services"].items() if "corenlp_local_credentials" in s.get("secrets", [])]
    assert sorted(local) == ["corenlp-gateway", "corenlp-provider"]
    assert not re.search(r"change-me|USERNAME|PASSWORD", str(compose_service("corenlp-gateway")))


def test_gateway_is_off_internal_and_only_its_callers_share_the_corenlp_network():
    compose = yaml.safe_load(COMPOSE.read_text())
    services = compose["services"]
    assert compose["networks"]["corenlp"]["internal"] is True
    members = sorted(n for n, s in services.items() if "corenlp" in s.get("networks", []))
    assert members == ["corenlp-gateway", "inconsistency-agent", "tlr-agent"]
    assert "internal" not in services["corenlp-gateway"]["networks"]
    assert sorted(services["corenlp-gateway"]["networks"]) == ["corenlp", "corenlp-local", "egress"]
    for outsider in ("plugin-stubs", "query-service", "orchestrator", "extraction-agent", "dashboard"):
        assert "corenlp" not in services[outsider]["networks"], outsider
    holders = [n for n, s in services.items() if "corenlp_client_token_tlr-agent" in s.get("secrets", [])]
    assert sorted(holders) == ["corenlp-gateway", "tlr-agent"]


def test_only_the_gateway_has_egress_and_the_provider_is_isolated():
    compose = yaml.safe_load(COMPOSE.read_text())
    services = compose["services"]
    with_egress = [n for n, s in services.items() if "egress" in s.get("networks", [])]
    assert "corenlp-gateway" in with_egress
    assert services["corenlp-provider"]["networks"] == ["corenlp-local"]
    assert compose["networks"]["corenlp-local"]["internal"] is True
    assert compose["networks"]["internal"]["internal"] is True
    assert services["inconsistency-agent"]["networks"] == ["internal", "corenlp"]
    assert services["tlr-agent"]["networks"] == ["internal", "corenlp"]
    for agent in ("tlr-agent", "inconsistency-agent"):
        secrets = services[agent].get("secrets", [])
        assert not [x for x in secrets if x.startswith("corenlp") and "client_token" not in x]
        assert "egress" not in services[agent]["networks"]
    assert "ports" not in services["corenlp-gateway"] and "ports" not in services["corenlp-provider"]
