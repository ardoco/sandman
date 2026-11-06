import type { Request, Response } from "express";
import type { Authenticator } from "./auth.ts";

export class StatusTracker {
  active = 0;
  lastActivity: string | undefined;

  constructor(
    readonly service: string,
    readonly version: string = process.env.SANDMAN_VERSION ?? "dev",
  ) {}

  private touch(): void {
    this.lastActivity = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  }

  async track<T>(fn: () => Promise<T>): Promise<T> {
    this.active++;
    this.touch();
    try {
      return await fn();
    } finally {
      this.active--;
      this.touch();
    }
  }

  snapshot(details?: Record<string, unknown>): Record<string, unknown> {
    return {
      service: this.service,
      version: this.version,
      state: this.active > 0 ? "working" : "idle",
      activeTasks: this.active,
      ...(this.lastActivity ? { lastActivityAt: this.lastActivity } : {}),
      ...(details && Object.keys(details).length > 0 ? { details } : {}),
    };
  }
}

export function statusHandler(
  tracker: StatusTracker,
  authenticate: Authenticator,
  details?: () => Record<string, unknown>,
) {
  return (req: Request, res: Response): void => {
    if (authenticate(req.headers.authorization) === undefined) {
      res.status(401).json({ error: "missing or invalid token" });
      return;
    }
    res.json(tracker.snapshot(details?.()));
  };
}
