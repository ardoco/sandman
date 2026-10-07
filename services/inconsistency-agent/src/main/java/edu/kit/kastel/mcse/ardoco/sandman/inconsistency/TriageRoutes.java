package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ServiceAuth;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status;
import io.vertx.core.Handler;
import io.vertx.ext.web.Router;
import io.vertx.ext.web.RoutingContext;
import io.vertx.ext.web.handler.BodyHandler;
import jakarta.annotation.PostConstruct;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.inject.Inject;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** REST: GET /v1/detectors and the triage API (contracts/openapi/inconsistency-agent.yaml); bearer service tokens. */
@ApplicationScoped
public class TriageRoutes {

    private static final Logger LOG = LoggerFactory.getLogger(TriageRoutes.class);
    private static final JsonMapper JSON = JsonMapper.shared();
    private static final Pattern PROJECT = Pattern.compile("[a-z0-9][a-z0-9-]{1,62}");
    private static final Pattern STABLE_ID = Pattern.compile("[a-z]+_[0-9a-f]{32}");
    private static final Pattern ACTOR = Pattern.compile("user:[A-Za-z0-9._@-]+");
    private static final Set<String> SEVERITIES = Set.of("info", "warning", "error");
    private static final Set<String> BODY_FIELDS = Set.of("to", "expectedStatus", "reason");

    @Inject
    Settings settings;

    @Inject
    TriageService triage;

    @Inject
    DetectorRegistry registry;

    private ServiceAuth auth;

    @PostConstruct
    void init() {
        auth = ServiceAuth.fromSettings(settings);
    }

    void routes(@Observes Router router) {
        router.get("/v1/detectors").blockingHandler(guarded(rc -> json(rc, 200, detectors())));
        router.get("/v1/projects/:projectId/inconsistencies").blockingHandler(guarded(this::list));
        router.get("/v1/inconsistencies/:id").blockingHandler(guarded(rc -> {
            String id = rc.pathParam("id");
            if (!STABLE_ID.matcher(id).matches()) {
                throw new TriageService.TriageException(404, "inconsistency " + id + " not found");
            }
            json(rc, 200, triage.detail(id));
        }));
        router.post("/v1/inconsistencies/:id/transitions")
                .handler(BodyHandler.create())
                .blockingHandler(guarded(this::transition));
    }

    private ObjectNode detectors() {
        ObjectNode out = JSON.createObjectNode();
        var array = out.putArray("detectors");
        for (InconsistencyDetector d : registry.all()) {
            ObjectNode n = array.addObject()
                    .put("id", d.id())
                    .put("version", d.version())
                    .put("kind", d.kind());
            new TreeSet<>(d.types()).forEach(n.putArray("types")::add);
            n.put("usesLlm", d.usesLlm());
            if (d.description() != null) {
                n.put("description", d.description());
            }
        }
        return out;
    }

    private void list(RoutingContext rc) {
        String project = rc.pathParam("projectId");
        require(PROJECT.matcher(project).matches(), "invalid projectId");
        Set<Status> status = null;
        if (rc.request().getParam("status") != null) {
            status = EnumSet.noneOf(Status.class);
            for (String s : rc.request().getParam("status").split(",", -1)) {
                status.add(enumValue(s));
            }
        }
        String severity = rc.request().getParam("severity");
        require(severity == null || SEVERITIES.contains(severity), "invalid severity");
        String artifact = rc.request().getParam("artifactId");
        require(artifact == null || STABLE_ID.matcher(artifact).matches(), "invalid artifactId");
        Instant since = null;
        try {
            if (rc.request().getParam("changedSince") != null) {
                since = Instant.parse(rc.request().getParam("changedSince"));
            }
        } catch (DateTimeParseException e) {
            throw new IllegalArgumentException("invalid changedSince");
        }
        int limit = rc.request().getParam("limit") == null
                ? 50
                : integer(rc.request().getParam("limit"));
        require(limit >= 1 && limit <= 500, "limit must be 1..500");
        int offset = rc.request().getParam("cursor") == null
                ? 0
                : integer(rc.request().getParam("cursor"));
        require(offset >= 0, "invalid cursor");
        json(
                rc,
                200,
                triage.list(
                        project,
                        new TriageService.Filter(
                                status, rc.request().getParam("type"), severity, artifact, since, limit, offset)));
    }

    private void transition(RoutingContext rc) {
        String caller = rc.get("caller");
        String actor = ServiceAuth.trustedActor(caller, rc.request().getHeader("X-Sandman-Actor"));
        require(actor != null && ACTOR.matcher(actor).matches(), "X-Sandman-Actor 'user:<name>' required");
        String id = rc.pathParam("id");
        require(STABLE_ID.matcher(id).matches(), "invalid inconsistency id");
        JsonNode body;
        try {
            body = JSON.readTree(rc.body().asString());
        } catch (RuntimeException e) {
            throw new IllegalArgumentException("body is not valid JSON");
        }
        require(body.isObject(), "body must be an object");
        List<String> fields = new ArrayList<>();
        body.propertyNames().forEach(fields::add);
        require(BODY_FIELDS.containsAll(fields), "unknown field in body");
        require(body.has("to") && body.has("expectedStatus"), "to and expectedStatus required");
        JsonNode reason = body.get("reason");
        require(reason == null || (reason.isString() && reason.asString().length() <= 2000), "invalid reason");
        json(
                rc,
                200,
                triage.transition(
                        id,
                        enumValue(body.get("to").asString("")),
                        enumValue(body.get("expectedStatus").asString("")),
                        reason == null ? null : reason.asString(),
                        actor));
    }

    private static Status enumValue(String s) {
        try {
            return Status.valueOf(s);
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("invalid status '" + s + "'");
        }
    }

    private static int integer(String s) {
        try {
            return Integer.parseInt(s);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("not an integer: " + s);
        }
    }

    private static void require(boolean ok, String message) {
        if (!ok) {
            throw new IllegalArgumentException(message);
        }
    }

    /** Authenticates the service token, then maps exceptions to problem+json. */
    private Handler<RoutingContext> guarded(Handler<RoutingContext> handler) {
        return rc -> {
            try {
                rc.put("caller", auth.requireService(rc.request().getHeader("Authorization"), false));
                handler.handle(rc);
            } catch (ServiceAuth.UnauthorizedException e) {
                problem(rc, 401, e.getMessage());
            } catch (ServiceAuth.ForbiddenException e) {
                problem(rc, 403, e.getMessage());
            } catch (TriageService.TriageException e) {
                problem(rc, e.status(), e.getMessage());
            } catch (IllegalArgumentException e) {
                problem(rc, 400, e.getMessage());
            } catch (RuntimeException e) {
                LOG.error("request failed", e);
                problem(rc, 500, "internal error");
            }
        };
    }

    private static void json(RoutingContext rc, int status, JsonNode body) {
        send(rc, status, "application/json", body);
    }

    private static void problem(RoutingContext rc, int status, String detail) {
        send(
                rc,
                status,
                "application/problem+json",
                JSON.createObjectNode()
                        .put("type", "about:blank")
                        .put("title", detail)
                        .put("status", status)
                        .put("detail", detail));
    }

    private static void send(RoutingContext rc, int status, String type, JsonNode body) {
        rc.response().setStatusCode(status).putHeader("Content-Type", type).end(body.toString());
    }
}
