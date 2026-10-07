package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aServer;
import io.vertx.ext.web.Router;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.inject.Inject;
import java.util.Comparator;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** GET /v1/approaches (ListApproachesOutput): the enabled approaches. */
@ApplicationScoped
public class ApproachesRoute {

    @Inject
    A2aServer server;

    @Inject
    Approaches approaches;

    void routes(@Observes Router router) {
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
        }
        return JsonMapper.shared().createObjectNode().set("approaches", list);
    }
}
