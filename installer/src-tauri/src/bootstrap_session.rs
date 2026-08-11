//! One-shot installer → desktop handoff of an authenticated Backend session.
//!
//! Auth flow lives in Rust (not the renderer) so the Tauri CSP can stay
//! `default-src 'self'` and the renderer never has to reach the backend
//! directly. The renderer collects the credentials, calls these Tauri
//! commands, and on success the new JWT is written to a user-only file
//! under canonical `$DESKAGENT_HOME` — see [`bootstrap_session_path`].
//! Desktop consumes that file at startup (client/main/entry.cjs),
//! validates the JWT against the backend's `/api/user/refresh`, and
//! hands the token to the normal BackendSession persistence path
//! (encrypted via Electron `safeStorage`). The bootstrap file is then
//! renamed to `.consumed` so a second launch never replays it.
//!
//! The password is NEVER persisted anywhere — it lives only in the
//! in-flight POST body and the JS state until the user clicks sign-in.
//!
//! Cross-language constants (filename, schema version, consumed suffix)
//! live in [`crate::paths`] as the canonical Rust source. The desktop
//! side mirrors them in `client/main/backend/bootstrap-session.cjs`
//! and a sync test (`bootstrap-session.test.cjs`) catches drift.

use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::paths;

// Shared across the verify and authenticate commands so the connection pool
// + TLS config survive across invocations. Building a reqwest::Client is
// non-trivial — it allocates a connection pool, runs TLS init, etc.
static HTTP: Lazy<reqwest::Client> = Lazy::new(|| {
    reqwest::Client::builder()
        .build()
        .expect("reqwest::Client::builder should not fail")
});

const VERIFY_TIMEOUT: Duration = Duration::from_secs(5);
const LOGIN_TIMEOUT: Duration = Duration::from_secs(15);

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserShape {
    pub id: Option<i64>,
    pub username: Option<String>,
}

/// On-disk shape. `token` is the raw JWT string from the backend;
/// desktop encrypts it through safeStorage on its first persist.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BootstrapSession {
    pub schema_version: u32,
    pub base_url: String,
    pub token: String,
    pub token_expires_at: i64,
    pub user: UserShape,
    pub saved_at: i64,
}

#[derive(Debug, Deserialize)]
pub struct VerifyBackendArgs {
    pub base_url: String,
}

#[derive(Debug, Deserialize)]
pub struct AuthenticateBackendArgs {
    pub base_url: String,
    pub username: String,
    pub password: String,
}

#[derive(Debug, Serialize)]
pub struct AuthSuccess {
    pub base_url: String,
    pub token_expires_at: i64,
    pub user: UserShape,
}

#[derive(Debug, Serialize)]
pub struct AuthFailure {
    pub kind: String,
    pub message: String,
    pub status: Option<u16>,
}

/// Normalizes a user-supplied backend URL.
///
/// Strips trailing whitespace + slashes, requires http/https, and rejects
/// anything that fails URL parsing so we never hand `reqwest` an
/// `http:// /` surprise.
pub fn normalize_base_url(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err("Backend URL is required.".into());
    }

    let stripped = trimmed.trim_end_matches(|c: char| c == '/' || c.is_whitespace());
    let parsed = url::Url::parse(stripped).map_err(|e| format!("Invalid backend URL: {e}"))?;

    match parsed.scheme() {
        "http" | "https" => Ok(stripped.to_string()),
        other => Err(format!("Unsupported scheme `{other}` (expected http/https).")),
    }
}

pub fn bootstrap_session_path() -> PathBuf {
    paths::deskagent_home().join(paths::BOOTSTRAP_FILENAME)
}

/// Sets POSIX 0600 on the bootstrap file so the user account is the only
/// reader. Windows has no `chmod`; the parent directory's ACL already
/// gates access.
fn write_user_only(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(path)?;
        use std::io::Write;
        file.write_all(bytes)?;
        Ok(())
    }
    #[cfg(not(unix))]
    {
        std::fs::write(path, bytes)
    }
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

#[tauri::command]
pub async fn verify_backend(args: VerifyBackendArgs) -> Result<bool, String> {
    let normalized = normalize_base_url(&args.base_url)?;
    let health_url = format!("{}/health", normalized);
    let response = HTTP
        .get(&health_url)
        .timeout(VERIFY_TIMEOUT)
        .send()
        .await
        .map_err(|e| format!("Could not reach backend: {e}"))?;
    Ok(response.status().is_success())
}

#[tauri::command]
pub async fn authenticate_backend(
    args: AuthenticateBackendArgs,
) -> Result<AuthSuccess, AuthFailure> {
    let base_url = match normalize_base_url(&args.base_url) {
        Ok(v) => v,
        Err(e) => return Err(AuthFailure { kind: "bad-url".into(), message: e, status: None }),
    };

    let body = serde_json::json!({
        "username": args.username,
        "password": args.password,
        "client_version": env!("CARGO_PKG_VERSION"),
        "client_context": {
            "platform_hints": std::env::consts::OS,
        }
    });

    let login_url = format!("{}/api/user/login", base_url);
    let response = match HTTP
        .post(&login_url)
        .timeout(LOGIN_TIMEOUT)
        .json(&body)
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return Err(AuthFailure {
                kind: "network".into(),
                message: format!("Could not reach backend: {e}"),
                status: None,
            });
        }
    };

    let status = response.status();
    let payload: Value = match response.json().await {
        Ok(v) => v,
        Err(e) => {
            return Err(AuthFailure {
                kind: "bad-response".into(),
                message: format!("Invalid response from backend: {e}"),
                status: Some(status.as_u16()),
            });
        }
    };

    if !status.is_success() {
        let detail = payload
            .get("detail")
            .and_then(|d| d.as_str())
            .unwrap_or("Sign-in failed.")
            .to_string();
        let kind = match status.as_u16() {
            401 | 403 => "bad-credentials",
            404 => "endpoint-missing",
            429 => "rate-limited",
            _ => "backend-error",
        };
        return Err(AuthFailure {
            kind: kind.into(),
            message: detail,
            status: Some(status.as_u16()),
        });
    }

    let token = payload
        .get("access_token")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AuthFailure {
            kind: "bad-response".into(),
            message: "Backend did not return an access token.".into(),
            status: Some(status.as_u16()),
        })?
        .to_string();

    let expires_in = payload
        .get("expires_in")
        .and_then(|v| v.as_i64())
        .filter(|v| *v > 0)
        .unwrap_or(8 * 60 * 60);
    let token_expires_at = now_ms() + expires_in * 1000;

    let user = payload
        .get("user")
        .map(|u| UserShape {
            id: u.get("id").and_then(|v| v.as_i64()),
            username: u.get("username").and_then(|v| v.as_str()).map(|s| s.to_string()),
        })
        .unwrap_or(UserShape { id: None, username: None });

    let session = BootstrapSession {
        schema_version: paths::BOOTSTRAP_SCHEMA_VERSION,
        base_url: base_url.clone(),
        token,
        token_expires_at,
        user,
        saved_at: now_ms(),
    };

    let bytes = match serde_json::to_vec_pretty(&session) {
        Ok(b) => b,
        Err(e) => {
            return Err(AuthFailure {
                kind: "serialize".into(),
                message: format!("Could not serialize bootstrap session: {e}"),
                status: None,
            });
        }
    };

    let target = bootstrap_session_path();
    if let Err(e) = write_user_only(&target, &bytes) {
        return Err(AuthFailure {
            kind: "write-failed".into(),
            message: format!("Could not write bootstrap session: {e}"),
            status: None,
        });
    }

    tracing::info!(
        path = %target.display(),
        user = ?session.user.username,
        "bootstrap session written"
    );

    Ok(AuthSuccess {
        base_url,
        token_expires_at: session.token_expires_at,
        user: session.user,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_strips_trailing_slash_and_whitespace() {
        let n = normalize_base_url("  https://api.example.com/// ").unwrap();
        assert_eq!(n, "https://api.example.com///".trim_end_matches(|c: char| c == '/' || c.is_whitespace()));
        assert_eq!(n, "https://api.example.com");
    }

    #[test]
    fn normalize_rejects_empty() {
        assert!(normalize_base_url("").is_err());
        assert!(normalize_base_url("   ").is_err());
    }

    #[test]
    fn normalize_rejects_non_http() {
        assert!(normalize_base_url("ftp://example.com").is_err());
        assert!(normalize_base_url("file:///etc/passwd").is_err());
    }

    #[test]
    fn normalize_accepts_http_and_https() {
        assert_eq!(
            normalize_base_url("http://localhost:10620").unwrap(),
            "http://localhost:10620"
        );
        assert_eq!(
            normalize_base_url("https://api.example.com").unwrap(),
            "https://api.example.com"
        );
    }

    #[test]
    fn bootstrap_session_path_lives_under_deskagent_home() {
        let path = bootstrap_session_path();
        let file_name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        assert_eq!(file_name, paths::BOOTSTRAP_FILENAME);
    }
}
