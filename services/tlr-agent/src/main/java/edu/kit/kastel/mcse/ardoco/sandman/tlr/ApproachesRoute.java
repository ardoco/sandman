package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ServiceAuth;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aServer;
import io.vertx.ext.web.Router;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.inject.Inject;
import java.util.Comparator;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** GET /v1/approaches (ListApproachesOutput): the enabled approaches; planned POST /v1/links/{linkId}/review (501). */
@ApplicationScoped
public class ApproachesRoute {

    @Inject
    A2aServer server;

    @Inject
    Approaches approaches;

    static final String NOT_IMPLEMENTED = "{\"type\":\"about:blank\",\"title\":\"Not Implemented\",\"status\":501,"
            + "\"detail\":\"POST /v1/links/{linkId}/review is planned (x-sandman-status planned)\"}";

    void routes(@Observes Router router) {
        router.post("/v1/links/:linkId/review").handler(rc -> {
            String caller = server.authenticate(rc.request().getHeader("Authorization"));
            if (caller == null) {
                rc.response()
                        .setStatusCode(401)
                        .putHeader("Content-Type", "application/json")
                        .end("{\"error\":\"missing or invalid token\"}");
                return;
            }
            try {
                ServiceAuth.trustedActor(caller, rc.request().getHeader("X-Sandman-Actor"));
            } catch (ServiceAuth.ForbiddenException e) {
                problem(rc, 403, "Forbidden", e.getMessage());
                return;
            }
            rc.response()
                    .setStatusCode(501)
                    .putHeader("Content-Type", "application/problem+json")
                    .end(NOT_IMPLEMENTED);
        });
        router.get("/v1/approaches").handler(rc -> {
            if (server.authenticate(rc.request().getHeader("Authorization")) == null) {
                rc.response()
                        .setStatusCode(401)
                        .putHeader("Content-Type", "application/json")
                        .end("{\"error\":\"missing or invalid token\"}");
                return;
            }
            rc.response()
                    .putHeader("Content-Type", "application/json")
                    .end(catalog().toString());
        });
    }

    private static void problem(io.vertx.ext.web.RoutingContext rc, int status, String title, String detail) {
        rc.response()
                .setStatusCode(status)
                .putHeader("Content-Type", "application/problem+json")
                .end(JsonMapper.shared()
                        .createObjectNode()
                        .put("type", "about:blank")
                        .put("title", title)
                        .put("status", status)
                        .put("detail", detail)
                        .toString());
    }

    ObjectNode catalog() {
        ArrayNode list = JsonMapper.shared().createArrayNode();
        for (var entry : approaches.enabled()) {
            TraceLinkApproach a = entry.approach();
            ObjectNode node = list.addObject();
            node.put("id", a.id());
            node.put("version", a.version());
            ArrayNode pairs = node.putArray("kindPairs");
            a.supports().stream()
                    .sorted(Comparator.comparing(KindPair::source).thenComparing(KindPair::target))
                    .forEach(p -> pairs.addObject().put("source", p.source()).put("target", p.target()));
            node.put("usesLlm", a.usesLlm());
            node.put("scopeMode", a.scopeMode().wire());
        }
        return JsonMapper.shared().createObjectNode().set("approaches", list);
    }
}
