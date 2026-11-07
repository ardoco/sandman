use std::future::Future;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};

pub fn now_rfc3339() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs()) as i64;
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

#[derive(Default)]
struct State {
    active: u32,
    last_activity: Option<String>,
}

pub struct StatusTracker {
    pub service: String,
    pub version: String,
    state: Mutex<State>,
}

impl StatusTracker {
    pub fn new(service: &str) -> Self {
        Self::with_version(
            service,
            &std::env::var("SANDMAN_VERSION").unwrap_or_else(|_| "dev".into()),
        )
    }

    pub fn with_version(service: &str, version: &str) -> Self {
        Self {
            service: service.into(),
            version: version.into(),
            state: Mutex::new(State::default()),
        }
    }

    fn change(&self, delta: i32) {
        let mut s = self.state.lock().unwrap();
        s.active = s.active.saturating_add_signed(delta);
        s.last_activity = Some(now_rfc3339());
    }

    pub async fn track<T>(&self, work: impl Future<Output = T>) -> T {
        struct Guard<'a>(&'a StatusTracker);
        impl Drop for Guard<'_> {
            fn drop(&mut self) {
                self.0.change(-1);
            }
        }
        self.change(1);
        let _guard = Guard(self);
        work.await
    }

    pub fn snapshot(&self, details: Option<Map<String, Value>>) -> Value {
        let s = self.state.lock().unwrap();
        let mut out = json!({
            "service": self.service,
            "version": self.version,
            "state": if s.active > 0 { "working" } else { "idle" },
            "activeTasks": s.active,
        });
        if let Some(t) = &s.last_activity {
            out["lastActivityAt"] = json!(t);
        }
        if let Some(d) = details.filter(|d| !d.is_empty()) {
            out["details"] = Value::Object(d);
        }
        out
    }
}
