package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import static java.nio.charset.StandardCharsets.UTF_8;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ServiceAuth;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.agent.StatusTracker;
import io.quarkus.arc.DefaultBean;
import io.vertx.core.Handler;
import io.vertx.ext.web.Router;
import io.vertx.ext.web.RoutingContext;
import jakarta.annotation.PostConstruct;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.enterprise.inject.Produces;
import jakarta.inject.Inject;
import jakarta.inject.Singleton;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import org.a2aproject.sdk.jsonrpc.common.json.JsonProcessingException;
import org.a2aproject.sdk.jsonrpc.common.json.JsonUtil;
import org.a2aproject.sdk.server.PublicAgentCard;
import org.a2aproject.sdk.spec.AgentCard;
import org.eclipse.microprofile.config.inject.ConfigProperty;

/**
 * Server wiring: Agent Card from {@code sandman.agent-card} (env SANDMAN_AGENT_CARD), bearer filter on the JSON-RPC
 * path, {@code GET /v1/status}, health endpoints. Settings, Contracts and StatusTracker are default beans an agent
 * may replace.
 */
@ApplicationScoped
public class A2aServer {

    static final String UNAUTHORIZED = "{\"error\":\"missing or invalid token\"}";

    @ConfigProperty(name = "sandman.agent-card")
    Path cardPath;

    @Inject
    Settings settings;

    @Inject
    StatusTracker status;

    private AgentCard card;
    private Map<String, Map<String, String>> schemas;
    private Set<String> plannedSkills = Set.of();
    private ServiceAuth auth;

    @PostConstruct
    void init() {
        try {
            card = JsonUtil.fromJson(Files.readString(cardPath), AgentCard.class);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("invalid agent card " + cardPath, e);
        }
        schemas = new HashMap<>();
        card.capabilities().extensions().stream()
                .filter(e -> SkillExecutor.EXTENSION.equals(e.uri()))
                .forEach(e -> schemas.putAll(SkillExecutor.cast(e.params().get("skills"))));
        plannedSkills = card.capabilities().extensions().stream()
                .filter(e -> SkillExecutor.EXTENSION.equals(e.uri()))
                .flatMap(
                        e -> SkillExecutor.<Map<String, Object>>cast(e.params().getOrDefault("plannedSkills", Map.of()))
                                .keySet()
                                .stream())
                .collect(Collectors.toUnmodifiableSet());
        auth = ServiceAuth.fromSettings(settings);
    }

    @Produces
    @PublicAgentCard
    AgentCard card() {
        return card;
    }

    /** Skill id -> {inputSchema, outputSchema} from the card's skill-invocation extension. */
    Map<String, Map<String, String>> schemas() {
        return schemas;
    }

    /** Skills declared in the card's plannedSkills: defined in the contract, answered with NOT_IMPLEMENTED. */
    Set<String> plannedSkills() {
        return plannedSkills;
    }

    /** Accepted caller of an Authorization header, or null. */
    public String authenticate(String authorization) {
        return caller(auth, settings.pluginInboundToken(), authorization);
    }

    /** Plugins check their inbound token (caller "owner"); agents verify service tokens, plugin callers rejected. */
    static String caller(ServiceAuth auth, String inboundToken, String authorization) {
        if (inboundToken != null) {
            String token = authorization != null && authorization.regionMatches(true, 0, "bearer ", 0, 7)
                    ? authorization.substring(7).strip()
                    : "";
            return !token.isEmpty() && MessageDigest.isEqual(token.getBytes(UTF_8), inboundToken.getBytes(UTF_8))
                    ? "owner"
                    : null;
        }
        String caller = auth.callerFromHeader(authorization);
        return caller == null || caller.startsWith(ServiceAuth.PLUGIN_PREFIX) ? null : caller;
    }

    /** a2a-java serves JSON-RPC at POST / only (W-1): guard both paths, reroute the card's path to /. */
    void routes(@Observes Router router) {
        String path = URI.create(card.supportedInterfaces().get(0).url()).getPath();
        String rpcPath = path == null || path.isEmpty() ? "/" : path;
        Handler<RoutingContext> guard = rc -> {
            if (authenticate(rc.request().getHeader("Authorization")) == null) {
                json(rc, 401, UNAUTHORIZED);
            } else if (!"/".equals(rc.normalizedPath())) {
                rc.reroute("/");
            } else {
                rc.next();
            }
        };
        router.post("/").order(-100).handler(guard);
        if (!"/".equals(rpcPath)) {
            router.post(rpcPath).order(-100).handler(guard);
        }
        router.get("/v1/status").handler(rc -> {
            if (authenticate(rc.request().getHeader("Authorization")) == null) {
                json(rc, 401, UNAUTHORIZED);
            } else {
                json(rc, 200, status.snapshot(null).toString());
            }
        });
        // ponytail: liveness only; add dependency checks to /readyz when agents need them
        Handler<RoutingContext> health = rc -> {
            if (SkillExecutor.draining()) {
                json(rc, 503, "{\"status\":\"draining\"}");
            } else {
                json(rc, 200, "{\"status\":\"ok\"}");
            }
        };
        router.get("/healthz").handler(health);
        router.get("/readyz").handler(health);
    }

    private static void json(RoutingContext rc, int code, String body) {
        rc.response()
                .setStatusCode(code)
                .putHeader("Content-Type", "application/json")
                .end(body);
    }

    @Produces
    @DefaultBean
    @Singleton
    static Settings defaultSettings() {
        return Settings.fromEnv();
    }

    @Produces
    @DefaultBean
    @Singleton
    static Contracts defaultContracts(Settings settings) {
        return new Contracts(settings.contractsDir());
    }

    @Produces
    @DefaultBean
    @Singleton
    static StatusTracker defaultStatus(Settings settings) {
        return new StatusTracker(settings.serviceName());
    }
}
