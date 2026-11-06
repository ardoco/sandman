import { timingSafeEqual } from "node:crypto";
import { sha256Hex } from "./ids.ts";

export const QUERY_SERVICE = "query-service";
export const ORCHESTRATOR = "orchestrator";
export const PLUGIN_PREFIX = "plugin:";

export class TokenVerifier {
  private readonly hashes: [string, string][];

  constructor(hashes: Record<string, string>) {
    this.hashes = Object.entries(hashes);
  }

  caller(token: string | undefined): string | undefined {
    if (!token) return undefined;
    const digest = Buffer.from(sha256Hex(token));
    for (const [name, expected] of this.hashes) {
      const e = Buffer.from(expected);
      if (e.length === digest.length && timingSafeEqual(digest, e)) return name;
    }
    return undefined;
  }

  callerFromHeader(authorization: string | undefined): string | undefined {
    const token = bearerToken(authorization);
    return token === undefined ? undefined : this.caller(token);
  }
}

export function bearerToken(authorization: string | undefined): string | undefined {
  if (!authorization || !authorization.toLowerCase().startsWith("bearer ")) return undefined;
  return authorization.slice(7).trim();
}

export class ForbiddenError extends Error {}

export function actorFromMetadata(caller: string, metadata: Record<string, unknown>): string | undefined {
  const actor = metadata["sandman.actor"];
  if (actor === undefined || actor === null) return undefined;
  if (caller !== ORCHESTRATOR) throw new ForbiddenError("sandman.actor is accepted only from the orchestrator");
  return typeof actor === "string" ? actor : undefined;
}

export type Authenticator = (authorization: string | undefined) => string | undefined;

export function authenticatorFor(settings: {
  pluginInboundToken?: string;
  serviceVerify: Record<string, string>;
}): Authenticator {
  const inbound = settings.pluginInboundToken;
  if (inbound) {
    const want = Buffer.from(sha256Hex(inbound));
    return (authorization) => {
      const token = bearerToken(authorization);
      if (!token) return undefined;
      const got = Buffer.from(sha256Hex(token));
      return timingSafeEqual(got, want) ? "owner" : undefined;
    };
  }
  const verifier = new TokenVerifier(settings.serviceVerify);
  return (authorization) => {
    const caller = verifier.callerFromHeader(authorization);
    return caller === undefined || caller.startsWith(PLUGIN_PREFIX) ? undefined : caller;
  };
}
