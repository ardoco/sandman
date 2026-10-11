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
    try:
        container.start()
    except Exception as e:
        server.shutdown()
        pytest.skip(f"docker unavailable: {e}")
    host, port = container.get_container_host_ip(), int(container.get_exposed_port(8080))
    for _ in range(60):
        try:
            if request(host, port, "GET", "/stanfordnlp/health")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    Upstream.seen = []
    yield host, port, server.server_port
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
    host, port, _ = gateway
    before = len(Upstream.seen)
    status, _ = request(host, port, method, path, {"Authorization": "Basic YWRtaW46Y2hhbmdlbWU="})
    assert status in (400, 404), (method, path, status)
    assert Upstream.seen[before:] == [], (method, path)


@pytest.mark.parametrize(("method", "path"), ALLOWED)
@pytest.mark.parametrize(
    "supplied", [None, "Basic YWdlbnQ6c3VwcGxpZWQ=", "Bearer agent-token", "basic YWdlbnQ6c3VwcGxpZWQ="]
)
def test_allowed_requests_carry_only_the_gateway_credentials(gateway, method, path, supplied):
    host, port, upstream_port = gateway
    before = len(Upstream.seen)
    headers = {"Authorization": supplied} if supplied else {}
    status, body = request(host, port, method, path, headers, b'{"text": "Hi."}' if method == "POST" else None)
    assert (status, body) == (200, b"upstream")
    assert Upstream.seen[before:] == [(method, path, BASIC, f"host.docker.internal:{upstream_port}")]


def test_duplicate_authorization_headers_are_all_replaced(gateway):
    host, port, _ = gateway
    before = len(Upstream.seen)
    conn = http.client.HTTPConnection(host, port, timeout=30)
    conn.putrequest("GET", "/stanfordnlp/health")
    conn.putheader("Authorization", "Basic YQ==")
    conn.putheader("Authorization", "Basic Yg==")
    conn.endheaders()
    assert conn.getresponse().status == 200
    assert [s[2] for s in Upstream.seen[before:]] == [BASIC]


def test_credentials_are_in_no_compose_file_or_container_setting():
    compose = yaml.safe_load(COMPOSE.read_text())
    assert "corenlp_credentials" in compose["secrets"]
    users = [n for n, s in compose["services"].items() if "corenlp_credentials" in s.get("secrets", [])]
    assert users == ["corenlp-gateway"]
    local = [n for n, s in compose["services"].items() if "corenlp_local_credentials" in s.get("secrets", [])]
    assert sorted(local) == ["corenlp-gateway", "corenlp-provider"]
    assert not re.search(r"change-me|USERNAME|PASSWORD", str(compose_service("corenlp-gateway")))


def test_only_the_gateway_has_egress_and_the_provider_is_isolated():
    compose = yaml.safe_load(COMPOSE.read_text())
    services = compose["services"]
    with_egress = [n for n, s in services.items() if "egress" in s.get("networks", [])]
    assert "corenlp-gateway" in with_egress
    assert services["corenlp-provider"]["networks"] == ["corenlp-local"]
    assert compose["networks"]["corenlp-local"]["internal"] is True
    assert compose["networks"]["internal"]["internal"] is True
    for agent in ("tlr-agent", "inconsistency-agent"):
        assert services[agent]["networks"] == ["internal"]
        assert "corenlp" not in str(services[agent].get("secrets", []))
    assert "ports" not in services["corenlp-gateway"] and "ports" not in services["corenlp-provider"]
