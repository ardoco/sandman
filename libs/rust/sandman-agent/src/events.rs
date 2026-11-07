use std::collections::HashMap;
use std::time::Duration;

use async_trait::async_trait;
use bytes::Bytes;
use futures_util::StreamExt;
use serde_json::{Map, Value, json};

use crate::contracts::Contracts;
use crate::status::now_rfc3339;
use crate::telemetry::{trace_headers, with_span};

pub const STREAM: &str = "SANDMAN";
pub const STREAM_MAX_AGE: Duration = Duration::from_secs(90 * 24 * 3600);
pub const STREAM_DUPLICATE_WINDOW: Duration = Duration::from_secs(120);
pub const CONSUMER_BACKOFF: [u64; 4] = [5, 30, 120, 300];
pub const CONSUMER_ACK_WAIT: Duration = Duration::from_secs(60);
pub const CONSUMER_MAX_DELIVER: i64 = 5;

#[derive(Debug)]
pub struct PublishError(pub String);

impl std::fmt::Display for PublishError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for PublishError {}

#[derive(Debug, Clone, PartialEq)]
pub struct Event {
    pub id: String,
    pub r#type: String,
    pub source: String,
    pub subject: String,
    pub time: String,
    pub data: Map<String, Value>,
    pub correlation_id: Option<String>,
    pub causation_id: Option<String>,
    pub traceparent: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct PublishOptions {
    pub correlation_id: Option<String>,
    pub causation_id: Option<String>,
    pub event_id: Option<String>,
}

#[async_trait]
pub trait EventSink: Send + Sync {
    async fn publish(
        &self,
        r#type: &str,
        subject: &str,
        data: Map<String, Value>,
        options: PublishOptions,
    ) -> Result<Event, PublishError>;
}

pub fn event_to_json(event: &Event) -> Value {
    let mut envelope = json!({
        "specversion": "1.0",
        "id": event.id,
        "source": event.source,
        "type": event.r#type,
        "subject": event.subject,
        "time": event.time,
        "datacontenttype": "application/json",
        "data": event.data,
    });
    for (key, value) in [
        ("correlationid", &event.correlation_id),
        ("causationid", &event.causation_id),
        ("traceparent", &event.traceparent),
    ] {
        if let Some(v) = value {
            envelope[key] = json!(v);
        }
    }
    envelope
}

pub fn event_from_json(envelope: &Value) -> Result<Event, String> {
    let text = |k: &str| {
        envelope[k]
            .as_str()
            .map(str::to_string)
            .ok_or_else(|| format!("event member {k} missing"))
    };
    let opt = |k: &str| envelope[k].as_str().map(str::to_string);
    Ok(Event {
        id: text("id")?,
        r#type: text("type")?,
        source: text("source")?,
        subject: text("subject")?,
        time: text("time")?,
        data: envelope["data"].as_object().cloned().unwrap_or_default(),
        correlation_id: opt("correlationid"),
        causation_id: opt("causationid"),
        traceparent: opt("traceparent"),
    })
}

pub fn build_event(
    contracts: &Contracts,
    source: &str,
    r#type: &str,
    subject: &str,
    data: Map<String, Value>,
    options: PublishOptions,
) -> Result<Event, String> {
    let event = Event {
        id: options
            .event_id
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string()),
        r#type: r#type.into(),
        source: format!("sandman/{source}"),
        subject: subject.into(),
        time: now_rfc3339(),
        data,
        correlation_id: options.correlation_id,
        causation_id: options.causation_id,
        traceparent: trace_headers().remove("traceparent"),
    };
    contracts
        .validate("events.schema.json", &event_to_json(&event))
        .map_err(|e| e.to_string())?;
    Ok(event)
}

/// Seam over JetStream publishing; the real implementation wraps `async_nats::jetstream::Context`.
#[async_trait]
pub trait JetPublish: Send + Sync {
    async fn publish(&self, subject: &str, payload: Bytes, msg_id: &str) -> Result<(), String>;
}

#[async_trait]
impl JetPublish for async_nats::jetstream::Context {
    async fn publish(&self, subject: &str, payload: Bytes, msg_id: &str) -> Result<(), String> {
        let mut headers = async_nats::HeaderMap::new();
        headers.insert(async_nats::header::NATS_MESSAGE_ID, msg_id);
        self.publish_with_headers(subject.to_string(), headers, payload)
            .await
            .map_err(|e| e.to_string())?
            .await
            .map_err(|e| e.to_string())?;
        Ok(())
    }
}

pub struct EventPublisher<J: JetPublish> {
    js: J,
    contracts: std::sync::Arc<Contracts>,
    source: String,
    backoff: Vec<Duration>,
}

impl<J: JetPublish> EventPublisher<J> {
    pub fn new(js: J, contracts: std::sync::Arc<Contracts>, source: &str) -> Self {
        let backoff = vec![
            Duration::from_millis(500),
            Duration::from_secs(2),
            Duration::from_secs(5),
        ];
        Self {
            js,
            contracts,
            source: source.into(),
            backoff,
        }
    }

    pub fn with_backoff(mut self, backoff: Vec<Duration>) -> Self {
        self.backoff = backoff;
        self
    }
}

#[async_trait]
impl<J: JetPublish> EventSink for EventPublisher<J> {
    async fn publish(
        &self,
        r#type: &str,
        subject: &str,
        data: Map<String, Value>,
        options: PublishOptions,
    ) -> Result<Event, PublishError> {
        let attributes = vec![opentelemetry::KeyValue::new(
            "sandman.event.type",
            r#type.to_string(),
        )];
        with_span("sandman.event.publish", attributes, None, async {
            let event = build_event(
                &self.contracts,
                &self.source,
                r#type,
                subject,
                data,
                options,
            )
            .map_err(PublishError)?;
            let payload = Bytes::from(event_to_json(&event).to_string());
            for attempt in 0.. {
                match self.js.publish(r#type, payload.clone(), &event.id).await {
                    Ok(()) => return Ok(event),
                    Err(cause) => match self.backoff.get(attempt) {
                        Some(delay) => tokio::time::sleep(*delay).await,
                        None => {
                            return Err(PublishError(format!(
                                "publishing {} {} failed after {} attempts: {cause}",
                                r#type,
                                event.id,
                                attempt + 1
                            )));
                        }
                    },
                }
            }
            unreachable!()
        })
        .await
    }
}

pub async fn connect_jetstream(nats_url: &str) -> Result<async_nats::jetstream::Context, String> {
    let client = async_nats::connect(nats_url)
        .await
        .map_err(|e| e.to_string())?;
    Ok(async_nats::jetstream::new(client))
}

pub fn stream_config() -> async_nats::jetstream::stream::Config {
    use async_nats::jetstream::stream::{Config, DiscardPolicy, RetentionPolicy, StorageType};
    Config {
        name: STREAM.into(),
        subjects: vec!["sandman.>".into()],
        storage: StorageType::File,
        retention: RetentionPolicy::Limits,
        max_age: STREAM_MAX_AGE,
        duplicate_window: STREAM_DUPLICATE_WINDOW,
        discard: DiscardPolicy::Old,
        num_replicas: 1,
        ..Default::default()
    }
}

pub async fn ensure_stream(js: &async_nats::jetstream::Context) -> Result<(), String> {
    js.get_or_create_stream(stream_config())
        .await
        .map_err(|e| e.to_string())?;
    js.update_stream(stream_config())
        .await
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn consumer_config(
    durable: &str,
    subject: &str,
) -> async_nats::jetstream::consumer::pull::Config {
    use async_nats::jetstream::consumer::AckPolicy;
    async_nats::jetstream::consumer::pull::Config {
        durable_name: Some(durable.into()),
        filter_subject: subject.into(),
        ack_policy: AckPolicy::Explicit,
        ack_wait: CONSUMER_ACK_WAIT,
        max_deliver: CONSUMER_MAX_DELIVER,
        backoff: CONSUMER_BACKOFF
            .iter()
            .map(|s| Duration::from_secs(*s))
            .collect(),
        max_ack_pending: 100,
        ..Default::default()
    }
}

/// Seam over a delivered JetStream message.
#[async_trait]
pub trait Delivery: Send {
    fn data(&self) -> &[u8];
    fn delivery_count(&self) -> u64;
    async fn ack(&mut self);
    async fn nak(&mut self, delay: Duration);
}

pub type Handler = Box<
    dyn Fn(Event) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<(), String>> + Send>>
        + Send
        + Sync,
>;

pub fn nak_delay(delivery_count: u64) -> Duration {
    let index = (delivery_count.max(1) as usize).min(CONSUMER_BACKOFF.len()) - 1;
    Duration::from_secs(CONSUMER_BACKOFF[index])
}

pub async fn handle_message(msg: &mut dyn Delivery, handler: &Handler) {
    let outcome: Result<(), String> = async {
        let envelope: Value = serde_json::from_slice(msg.data()).map_err(|e| e.to_string())?;
        let event = event_from_json(&envelope)?;
        let parent: Option<HashMap<String, String>> = event
            .traceparent
            .clone()
            .map(|t| HashMap::from([("traceparent".to_string(), t)]));
        let attributes = vec![
            opentelemetry::KeyValue::new("sandman.event.type", event.r#type.clone()),
            opentelemetry::KeyValue::new("sandman.event.id", event.id.clone()),
        ];
        with_span(
            "sandman.event.consume",
            attributes,
            parent.as_ref(),
            handler(event),
        )
        .await
    }
    .await;
    match outcome {
        Ok(()) => msg.ack().await,
        Err(e) => {
            eprintln!("event handler failed: {e}");
            msg.nak(nak_delay(msg.delivery_count())).await;
        }
    }
}

struct NatsDelivery(async_nats::jetstream::Message);

#[async_trait]
impl Delivery for NatsDelivery {
    fn data(&self) -> &[u8] {
        &self.0.payload
    }
    fn delivery_count(&self) -> u64 {
        self.0.info().map_or(1, |i| i.delivered as u64)
    }
    async fn ack(&mut self) {
        let _ = self.0.ack().await;
    }
    async fn nak(&mut self, delay: Duration) {
        let _ = self
            .0
            .ack_with(async_nats::jetstream::AckKind::Nak(Some(delay)))
            .await;
    }
}

pub async fn run_consumer(
    js: &async_nats::jetstream::Context,
    durable: &str,
    handler: Handler,
    mut shutdown: tokio::sync::watch::Receiver<bool>,
    subject: &str,
) -> Result<(), String> {
    let stream = js.get_stream(STREAM).await.map_err(|e| e.to_string())?;
    let consumer = stream
        .get_or_create_consumer(durable, consumer_config(durable, subject))
        .await
        .map_err(|e| e.to_string())?;
    let mut messages = consumer.messages().await.map_err(|e| e.to_string())?;
    loop {
        tokio::select! {
            _ = shutdown.changed() => return Ok(()),
            next = messages.next() => match next {
                Some(Ok(msg)) => handle_message(&mut NatsDelivery(msg), &handler).await,
                Some(Err(e)) => eprintln!("consumer error: {e}"),
                None => return Ok(()),
            },
        }
    }
}
