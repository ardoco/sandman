import { context, propagation, SpanStatusCode, trace, type Attributes } from "@opentelemetry/api";

const tracer = trace.getTracer("sandman-agent");

export async function withSpan<T>(
  name: string,
  attributes: Attributes,
  fn: () => Promise<T>,
  parent?: Record<string, string>,
): Promise<T> {
  const ctx = parent ? propagation.extract(context.active(), parent) : context.active();
  return tracer.startActiveSpan(name, { attributes }, ctx, async (span) => {
    try {
      return await fn();
    } catch (e) {
      span.setStatus({ code: SpanStatusCode.ERROR, message: (e as Error).message });
      throw e;
    } finally {
      span.end();
    }
  });
}

export function traceHeaders(): Record<string, string> {
  const carrier: Record<string, string> = {};
  propagation.inject(context.active(), carrier);
  return carrier;
}
