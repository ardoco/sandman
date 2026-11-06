import { randomUUID } from "node:crypto";
import {
  AckPolicy,
  DiscardPolicy,
  RetentionPolicy,
  StorageType,
  jetstream,
  jetstreamManager,
  type ConsumerConfig,
  type JetStreamClient,
  type JetStreamManager,
  type JsMsg,
  type StreamConfig,
} from "@nats-io/jetstream";
import { connect } from "@nats-io/transport-node";
import type { Contracts } from "./contracts.ts";
import { traceHeaders, withSpan } from "./telemetry.ts";

export const STREAM = "SANDMAN";
const nanos = (seconds: number): number => seconds * 1e9;
export const STREAM_CONFIG: Partial<StreamConfig> & { name: string } = {
  name: STREAM,
  subjects: ["sandman.>"],
  storage: StorageType.File,
  retention: RetentionPolicy.Limits,
  max_age: nanos(90 * 24 * 3600),
  duplicate_window: nanos(120),
  discard: DiscardPolicy.Old,
  num_replicas: 1,
};
export const CONSUMER_BACKOFF = [5, 30, 120, 300];

export class PublishError extends Error {}

export interface Event {
  id: string;
  type: string;
  source: string;
  subject: string;
  time: string;
  data: Record<string, unknown>;
  correlationId?: string;
  causationId?: string;
  traceparent?: string;
}

export interface PublishOptions {
  correlationId?: string;
  causationId?: string;
  eventId?: string;
}

export interface EventSink {
  publish(type: string, subject: string, data: Record<string, unknown>, options?: PublishOptions): Promise<Event>;
}

export function eventToJson(event: Event): Record<string, unknown> {
  const envelope: Record<string, unknown> = {
    specversion: "1.0",
    id: event.id,
    source: event.source,
    type: event.type,
    subject: event.subject,
    time: event.time,
    datacontenttype: "application/json",
    data: event.data,
  };
  if (event.correlationId !== undefined) envelope.correlationid = event.correlationId;
  if (event.causationId !== undefined) envelope.causationid = event.causationId;
  if (event.traceparent !== undefined) envelope.traceparent = event.traceparent;
  return envelope;
}

export function eventFromJson(envelope: Record<string, unknown>): Event {
  const text = (k: string): string => {
    const v = envelope[k];
    if (typeof v !== "string") throw new TypeError(`event member ${k} missing`);
    return v;
  };
  const opt = (k: string): string | undefined => (typeof envelope[k] === "string" ? (envelope[k] as string) : undefined);
  return {
    id: text("id"),
    type: text("type"),
    source: text("source"),
    subject: text("subject"),
    time: text("time"),
    data: (envelope.data ?? {}) as Record<string, unknown>,
    correlationId: opt("correlationid"),
    causationId: opt("causationid"),
    traceparent: opt("traceparent"),
  };
}

export function buildEvent(
  contracts: Contracts,
  source: string,
  type: string,
  subject: string,
  data: Record<string, unknown>,
  options: PublishOptions = {},
): Event {
  const event: Event = {
    id: options.eventId ?? randomUUID(),
    type,
    source: `sandman/${source}`,
    subject,
    time: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
    data,
    correlationId: options.correlationId,
    causationId: options.causationId,
    traceparent: traceHeaders().traceparent,
  };
  contracts.validate("events.schema.json", eventToJson(event));
  return event;
}

export class EventPublisher implements EventSink {
  constructor(
    private readonly js: Pick<JetStreamClient, "publish">,
    private readonly contracts: Contracts,
    private readonly source: string,
    private readonly backoffMs: readonly number[] = [500, 2000, 5000],
  ) {}

  async publish(
    type: string,
    subject: string,
    data: Record<string, unknown>,
    options: PublishOptions = {},
  ): Promise<Event> {
    return withSpan("sandman.event.publish", { "sandman.event.type": type }, async () => {
      const event = buildEvent(this.contracts, this.source, type, subject, data, options);
      const payload = JSON.stringify(eventToJson(event));
      for (let attempt = 0; ; attempt++) {
        try {
          await this.js.publish(type, payload, { msgID: event.id });
          return event;
        } catch (e) {
          const delay = this.backoffMs[attempt];
          if (delay === undefined) {
            throw new PublishError(`publishing ${type} ${event.id} failed after ${attempt + 1} attempts`, { cause: e });
          }
          await new Promise((r) => setTimeout(r, delay));
        }
      }
    });
  }
}

export interface JetStreamHandle {
  js: JetStreamClient;
  jsm: JetStreamManager;
  close(): Promise<void>;
}

export async function connectJetStream(natsUrl: string): Promise<JetStreamHandle> {
  const nc = await connect({ servers: natsUrl });
  return { js: jetstream(nc), jsm: await jetstreamManager(nc), close: () => nc.close() };
}

export async function ensureStream(jsm: Pick<JetStreamManager, "streams">): Promise<void> {
  try {
    await jsm.streams.info(STREAM);
  } catch {
    await jsm.streams.add(STREAM_CONFIG);
    return;
  }
  await jsm.streams.update(STREAM, STREAM_CONFIG);
}

export type Handler = (event: Event) => Promise<void>;

export function consumerConfig(durable: string, subject = "sandman.>"): Partial<ConsumerConfig> {
  return {
    durable_name: durable,
    filter_subject: subject,
    ack_policy: AckPolicy.Explicit,
    ack_wait: nanos(60),
    max_deliver: 5,
    backoff: CONSUMER_BACKOFF.map(nanos),
    max_ack_pending: 100,
  };
}

export async function handleMessage(
  msg: Pick<JsMsg, "data" | "ack" | "nak"> & { info?: { deliveryCount: number } },
  handler: Handler,
): Promise<void> {
  try {
    const event = eventFromJson(JSON.parse(Buffer.from(msg.data).toString("utf-8")) as Record<string, unknown>);
    await withSpan(
      "sandman.event.consume",
      { "sandman.event.type": event.type, "sandman.event.id": event.id },
      () => handler(event),
      event.traceparent ? { traceparent: event.traceparent } : undefined,
    );
  } catch (e) {
    console.error("event handler failed", e);
    const delivered = msg.info?.deliveryCount || 1;
    msg.nak(CONSUMER_BACKOFF[Math.min(delivered, CONSUMER_BACKOFF.length) - 1]! * 1000);
    return;
  }
  msg.ack();
}

export async function runConsumer(
  handle: Pick<JetStreamHandle, "js" | "jsm">,
  durable: string,
  handler: Handler,
  signal: AbortSignal,
  subject = "sandman.>",
): Promise<void> {
  await handle.jsm.consumers.add(STREAM, consumerConfig(durable, subject));
  const consumer = await handle.js.consumers.get(STREAM, durable);
  while (!signal.aborted) {
    const batch = await consumer.fetch({ max_messages: 10, expires: 2000 });
    for await (const msg of batch) await handleMessage(msg, handler);
  }
}
