package edu.kit.kastel.mcse.ardoco.sandman.agent;

import static java.nio.charset.StandardCharsets.UTF_8;

import java.security.MessageDigest;
import java.util.Map;

/** Own service token, verification of presented tokens and trusted forwarders (ADR-0013 amendment). */
public final class ServiceAuth {

    public static final String QUERY_SERVICE = "query-service";
    public static final String ORCHESTRATOR = "orchestrator";
    public static final String PLUGIN_PREFIX = "plugin:";

    /** Missing or unknown token (HTTP 401). */
    public static class UnauthorizedException extends RuntimeException {
        public UnauthorizedException(String message) {
            super(message);
        }
    }

    /** Authenticated caller not allowed here (HTTP 403). */
    public static class ForbiddenException extends RuntimeException {
        public ForbiddenException(String message) {
            super(message);
        }
    }

    private final Map<String, String> hashes; // caller -> sha256(token)
    private final String ownToken;

    public ServiceAuth(Map<String, String> hashes, String ownToken) {
        this.hashes = Map.copyOf(hashes);
        this.ownToken = ownToken;
    }

    public static ServiceAuth fromSettings(Settings settings) {
        return new ServiceAuth(settings.serviceVerify(), settings.serviceToken());
    }

    /** This service's token (SANDMAN_SERVICE_TOKEN_FILE), presented when calling other services. */
    public String ownToken() {
        return ownToken;
    }

    /** Caller name of a token, or null. */
    public String caller(String token) {
        if (token == null || token.isEmpty()) {
            return null;
        }
        byte[] digest = Ids.sha256Hex(token).getBytes(UTF_8);
        for (Map.Entry<String, String> e : hashes.entrySet()) {
            if (MessageDigest.isEqual(digest, e.getValue().getBytes(UTF_8))) {
                return e.getKey();
            }
        }
        return null;
    }

    /** Caller name from an 'Authorization: Bearer ...' header, or null. */
    public String callerFromHeader(String authorization) {
        if (authorization == null || !authorization.regionMatches(true, 0, "bearer ", 0, 7)) {
            return null;
        }
        return caller(authorization.substring(7).strip());
    }

    /** Authenticated caller; plugin callers are rejected unless explicitly allowed. */
    public String requireService(String authorization, boolean allowPlugins) {
        String caller = callerFromHeader(authorization);
        if (caller == null) {
            throw new UnauthorizedException("missing or invalid service token");
        }
        if (caller.startsWith(PLUGIN_PREFIX) && !allowPlugins) {
            throw new ForbiddenException("plugin callers are not allowed here");
        }
        return caller;
    }

    /** X-Sandman-Actor header value if sent by the query-service; rejected from any other caller. */
    public static String trustedActor(String caller, String actorHeader) {
        if (actorHeader == null) {
            return null;
        }
        if (!QUERY_SERVICE.equals(caller)) {
            throw new ForbiddenException("X-Sandman-Actor is accepted only from the query-service");
        }
        return actorHeader;
    }

    /** A2A metadata sandman.actor, accepted only from the orchestrator (ForbiddenException from any other caller). */
    public static String actorFromMetadata(String caller, Map<String, ?> metadata) {
        Object actor = metadata.get("sandman.actor");
        if (actor == null) {
            return null;
        }
        if (!ORCHESTRATOR.equals(caller)) {
            throw new ForbiddenException("sandman.actor is accepted only from the orchestrator");
        }
        return actor instanceof String a ? a : null;
    }
}
