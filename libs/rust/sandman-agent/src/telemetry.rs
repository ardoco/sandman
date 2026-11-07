use std::collections::HashMap;
use std::future::Future;

use opentelemetry::global;
use opentelemetry::trace::{FutureExt, Status, TraceContextExt, Tracer};
use opentelemetry::{Context, KeyValue};

/// Runs `work` inside a span. `parent` is a W3C carrier (for example from an event `traceparent`).
pub async fn with_span<T, E: std::fmt::Display>(
    name: &'static str,
    attributes: Vec<KeyValue>,
    parent: Option<&HashMap<String, String>>,
    work: impl Future<Output = Result<T, E>>,
) -> Result<T, E> {
    let tracer = global::tracer("sandman-agent");
    let base = match parent {
        Some(carrier) => global::get_text_map_propagator(|p| p.extract(carrier)),
        None => Context::current(),
    };
    let span =
        tracer.build_with_context(tracer.span_builder(name).with_attributes(attributes), &base);
    let cx = base.with_span(span);
    let result = work.with_context(cx.clone()).await;
    if let Err(e) = &result {
        cx.span().set_status(Status::error(e.to_string()));
    }
    cx.span().end();
    result
}

pub fn trace_headers() -> HashMap<String, String> {
    let mut carrier = HashMap::new();
    global::get_text_map_propagator(|p| p.inject_context(&Context::current(), &mut carrier));
    carrier
}
