package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ServiceAuth;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aServer;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.EventSink;
import io.vertx.ext.web.Router;
import io.vertx.ext.web.RoutingContext;
import io.vertx.ext.web.handler.BodyHandler;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.inject.Inject;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Comparator;
import java.util.Set;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** GET /v1/approaches (ListApproachesOutput): the enabled approaches; POST /v1/links/{linkId}/review (T6.1). */
@ApplicationScoped
public class ApproachesRoute {

    private static final Logger LOG = LoggerFactory.getLogger(ApproachesRoute.class);

    @Inject
    A2aServer server;

    @Inject
    Approaches approaches;

    private static final Pattern LINK_ID = Pattern.compile("tl_[0-9a-f]{32}");
    private static final Pattern ACTOR = Pattern.compile("user:[A-Za-z0-9._@-]+");
    private static final Set<String> STATUSES = Set.of("confirmed", "rejected", "candidate");

    @Inject
    KgClient kg;

    @Inject
    EventSink events;

    private final java.util.concurrent.ScheduledExecutorService retryTimer =
            java.util.concurrent.Executors.newSingleThreadScheduledExecutor(r -> {
                Thread thread = new Thread(r, "review-event-retry");
                thread.setDaemon(true);
                return thread;
            });

    void routes(@Observes Router router) {
        retryTimer.scheduleWithFixedDelay(this::retryPending, 30, 30, java.util.concurrent.TimeUnit.SECONDS);
        router.post("/v1/links/:linkId/review").handler(BodyHandler.create()).blockingHandler(this::review);
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

    private void review(RoutingContext rc) {
        String caller = server.authenticate(rc.request().getHeader("Authorization"));
        if (caller == null) {
            rc.response()
                    .setStatusCode(401)
                    .putHeader("Content-Type", "application/json")
                    .end("{\"error\":\"missing or invalid token\"}");
            return;
        }
        String actor;
        try {
            actor = ServiceAuth.trustedActor(caller, rc.request().getHeader("X-Sandman-Actor"));
        } catch (ServiceAuth.ForbiddenException e) {
            problem(rc, 403, "Forbidden", e.getMessage());
            return;
        }
        if (actor == null || !ACTOR.matcher(actor).matches()) {
            problem(rc, 400, "Bad Request", "X-Sandman-Actor 'user:<name>' required");
            return;
        }
        String id = rc.pathParam("linkId");
        JsonNode body;
        try {
            body = JsonMapper.shared().readTree(rc.body().asString());
        } catch (RuntimeException e) {
            problem(rc, 400, "Bad Request", "body is not valid JSON");
            return;
        }
        JsonNode status = body.get("status");
        JsonNode reasonNode = body.get("reason");
        boolean valid = LINK_ID.matcher(id).matches()
                && body.isObject()
                && Set.of("status", "reason").containsAll(body.propertyNames())
                && status != null
                && status.isString()
                && STATUSES.contains(status.asString())
                && (reasonNode == null
                        || (reasonNode.isString() && reasonNode.asString().length() <= 2000));
        if (!valid) {
            problem(rc, 400, "Bad Request", "invalid link id or body");
            return;
        }
        String reason = reasonNode == null || reasonNode.asString().isBlank() ? null : reasonNode.asString();
        LinkStore store = new LinkStore(kg);
        var link = store.link(id);
        if (link.isEmpty()) {
            problem(rc, 404, "Not Found", "link " + id + " not found");
        } else if (!link.get().active()) {
            problem(rc, 409, "Conflict", "link " + id + " is not active");
        } else if (LinkStore.EXPLICIT_REFERENCE.equals(link.get().approach())) {
            problem(rc, 409, "Conflict", "explicit-reference links are not reviewed");
        } else if ("rejected".equals(status.asString()) && reason == null) {
            problem(rc, 422, "Unprocessable Entity", "reason required for rejected");
        } else {
            var reviewed = store.review(
                    link.get(),
                    status.asString(),
                    actor,
                    "candidate".equals(status.asString()) ? null : reason,
                    r -> eventData(link.get().projectId(), r, status.asString(), actor, reason)
                            .toString());
            retryPending();
            ObjectNode out =
                    JsonMapper.shared().createObjectNode().put("id", id).put("status", status.asString());
            reviewed.linkIds().forEach(out.putArray("reviewedLinkIds")::add);
            rc.response().putHeader("Content-Type", "application/json").end(out.toString());
        }
    }

    private static ObjectNode eventData(
            String project, LinkStore.Reviewed reviewed, String status, String actor, String reason) {
        ObjectNode data = JsonMapper.shared().createObjectNode().put("projectId", project);
        reviewed.linkIds().forEach(data.putArray("linkIds")::add);
        data.put("status", status).put("actor", actor);
        if (reason != null) {
            data.put("reason", reason);
        }
        data.put("at", Instant.now().truncatedTo(ChronoUnit.SECONDS).toString());
        data.putArray("sourceVersionIds").add(reviewed.sourceVersionId());
        data.putArray("targetVersionIds").add(reviewed.targetVersionId());
        return data;
    }

    /** Publishes the stored review events with their stable id; a failed publish stays stored for the next run. */
    synchronized void retryPending() {
        LinkStore store = new LinkStore(kg);
        try {
            for (var pending : store.pendingEvents()) {
                events.publish(
                        "sandman.tracelink.reviewed",
                        pending.projectId(),
                        JsonMapper.shared().readTree(pending.data()),
                        null,
                        null,
                        pending.id());
                store.deletePending(pending.id());
            }
        } catch (RuntimeException e) {
            LOG.error("publishing pending review events failed, retrying later", e);
        }
    }

    private static void problem(RoutingContext rc, int status, String title, String detail) {
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
