//! Typed guest client for Forge's per-boot Core ML and Metal bridge.

use reqwest::{Method, StatusCode};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fmt,
    fs::File,
    io::{self, Read},
    path::{Component, Path},
    time::Duration,
};

pub const DEFAULT_ENDPOINT: &str = "http://10.0.2.2:4777/accelerator/v1";
pub const PROTOCOL_VERSION: &str = "1.0";

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ComputeUnits {
    Cpu,
    CpuGpu,
    CpuAne,
    All,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct ScratchReference {
    pub relative_path: String,
    pub sha256: String,
    pub size: u64,
    pub media_type: String,
    pub delete_after_read: bool,
}

impl ScratchReference {
    pub fn from_file(
        path: impl AsRef<Path>,
        scratch_root: impl AsRef<Path>,
    ) -> Result<Self, Error> {
        let source = path.as_ref().canonicalize()?;
        let root = scratch_root.as_ref().canonicalize()?;
        let relative = source
            .strip_prefix(&root)
            .map_err(|_| Error::InvalidInput("scratch object is outside the shared root".into()))?;
        if !source.is_file()
            || relative.components().any(|part| {
                matches!(
                    part,
                    Component::ParentDir | Component::RootDir | Component::Prefix(_)
                )
            })
        {
            return Err(Error::InvalidInput(
                "scratch object must be a regular descendant file".into(),
            ));
        }
        let mut stream = File::open(&source)?;
        let mut hasher = Sha256::new();
        let mut buffer = [0_u8; 1024 * 1024];
        loop {
            let read = stream.read(&mut buffer)?;
            if read == 0 {
                break;
            }
            hasher.update(&buffer[..read]);
        }
        Ok(Self {
            relative_path: relative.to_string_lossy().replace('\\', "/"),
            sha256: format!("{:x}", hasher.finalize()),
            size: source.metadata()?.len(),
            media_type: "application/octet-stream".into(),
            delete_after_read: false,
        })
    }

    pub fn validate(&self) -> Result<(), Error> {
        let path = Path::new(&self.relative_path);
        if self.relative_path.is_empty()
            || path.is_absolute()
            || self
                .relative_path
                .split('/')
                .any(|segment| matches!(segment, "" | "." | ".."))
            || path
                .components()
                .any(|part| matches!(part, Component::ParentDir))
        {
            return Err(Error::InvalidInput(
                "scratch path must be a normalized relative path".into(),
            ));
        }
        if self.sha256.len() != 64
            || !self
                .sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(Error::InvalidInput(
                "sha256 must be lowercase hexadecimal".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "storage", rename_all = "snake_case")]
pub enum Tensor {
    Inline {
        dtype: String,
        shape: Vec<u64>,
        data_base64: String,
    },
    Scratch {
        dtype: String,
        shape: Vec<u64>,
        object: ScratchReference,
        #[serde(default)]
        byte_offset: u64,
        #[serde(skip_serializing_if = "Option::is_none")]
        byte_length: Option<u64>,
    },
}

#[derive(Clone, Debug, Deserialize)]
pub struct Limits {
    pub max_request_bytes: u64,
    pub max_inline_bytes: u64,
    pub max_scratch_object_bytes: u64,
    pub max_tensor_rank: u8,
    pub max_inputs: u16,
    pub max_outputs: u16,
    pub max_concurrent_jobs: u16,
    pub max_model_handles: u16,
    pub max_library_handles: u16,
    pub max_model_bytes: u64,
    pub max_metal_source_bytes: u64,
    pub max_buffer_bytes: u64,
    pub job_retention_seconds: u64,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Capabilities {
    pub protocol_version: String,
    pub server_version: String,
    pub boot_id: String,
    pub compute_units: Vec<ComputeUnits>,
    pub coreml: CoreMlCapabilities,
    pub metal: MetalCapabilities,
    pub scratch: ScratchCapabilities,
    pub limits: Limits,
}

#[derive(Clone, Debug, Deserialize)]
pub struct CoreMlCapabilities {
    pub available: bool,
    pub formats: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct MetalCapabilities {
    pub available: bool,
    pub language_version: String,
    pub families: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ScratchCapabilities {
    pub guest_root: String,
    pub requires_sha256: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Job {
    pub id: String,
    pub operation: String,
    pub state: JobState,
    pub progress: Option<f64>,
    pub result: Option<Value>,
    pub error: Option<BridgeErrorBody>,
}

impl Job {
    pub fn terminal(&self) -> bool {
        matches!(
            self.state,
            JobState::Succeeded | JobState::Failed | JobState::Cancelled
        )
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Queued,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Clone, Debug, Deserialize)]
pub struct JobEventPage {
    pub events: Vec<JobEvent>,
    pub next_after: u64,
    pub terminal: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct JobEvent {
    pub sequence: u64,
    pub at: String,
    pub kind: String,
    pub progress: Option<f64>,
    pub message: Option<String>,
    pub diagnostic: Option<Value>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct BridgeErrorBody {
    pub code: String,
    pub message: String,
    pub retriable: bool,
    pub request_id: String,
    #[serde(default)]
    pub details: Map<String, Value>,
}

#[derive(Debug)]
pub enum Error {
    InvalidInput(String),
    Transport(reqwest::Error),
    Io(io::Error),
    Protocol(String),
    Bridge {
        status: StatusCode,
        body: BridgeErrorBody,
    },
}

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidInput(message) | Self::Protocol(message) => formatter.write_str(message),
            Self::Transport(error) => error.fmt(formatter),
            Self::Io(error) => error.fmt(formatter),
            Self::Bridge { status, body } => {
                write!(formatter, "HTTP {status} {}: {}", body.code, body.message)
            }
        }
    }
}

impl std::error::Error for Error {}

impl From<reqwest::Error> for Error {
    fn from(value: reqwest::Error) -> Self {
        Self::Transport(value)
    }
}

impl From<io::Error> for Error {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

#[derive(Clone)]
pub struct Client {
    endpoint: String,
    token: String,
    http: reqwest::Client,
}

impl Client {
    pub fn new(token: impl Into<String>) -> Result<Self, Error> {
        Self::with_endpoint(token, DEFAULT_ENDPOINT, false)
    }

    pub fn with_endpoint(
        token: impl Into<String>,
        endpoint: impl Into<String>,
        allow_non_guest_endpoint: bool,
    ) -> Result<Self, Error> {
        let token = token.into();
        let endpoint = endpoint.into().trim_end_matches('/').to_owned();
        if token.len() < 32 || token.chars().any(char::is_whitespace) {
            return Err(Error::InvalidInput(
                "token must contain at least 32 non-whitespace characters".into(),
            ));
        }
        if endpoint != DEFAULT_ENDPOINT && !allow_non_guest_endpoint {
            return Err(Error::InvalidInput("non-guest endpoint refused".into()));
        }
        let url = reqwest::Url::parse(&endpoint)
            .map_err(|error| Error::InvalidInput(format!("invalid endpoint: {error}")))?;
        if !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(Error::InvalidInput(
                "endpoint may not contain credentials, query, or fragment".into(),
            ));
        }
        let http = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(30))
            .build()?;
        Ok(Self {
            endpoint,
            token,
            http,
        })
    }

    pub async fn capabilities(&self) -> Result<Capabilities, Error> {
        let capabilities: Capabilities = self.request(Method::GET, "/capabilities", None).await?;
        if capabilities.protocol_version != PROTOCOL_VERSION {
            return Err(Error::Protocol(format!(
                "host protocol {:?} is incompatible with {:?}",
                capabilities.protocol_version, PROTOCOL_VERSION
            )));
        }
        Ok(capabilities)
    }

    pub async fn verify_scratch(
        &self,
        object: &ScratchReference,
    ) -> Result<ScratchReference, Error> {
        object.validate()?;
        self.request(
            Method::POST,
            "/scratch/verify",
            Some(json!({ "object": object })),
        )
        .await
    }

    pub async fn compile_coreml(
        &self,
        source: &ScratchReference,
        format: &str,
        compute_units: Option<ComputeUnits>,
    ) -> Result<Job, Error> {
        source.validate()?;
        if format != "mlmodel" {
            return Err(Error::InvalidInput(
                "Forge accelerator v1 supports only regular-file mlmodel input".into(),
            ));
        }
        let mut body = json!({ "source": source, "format": format });
        if let Some(units) = compute_units {
            body["compute_units"] = serde_json::to_value(units).expect("enum serializes");
        }
        self.request(Method::POST, "/coreml/compilations", Some(body))
            .await
    }

    pub async fn predict_coreml(
        &self,
        model_id: &str,
        inputs: &BTreeMap<String, Tensor>,
        compute_units: Option<ComputeUnits>,
    ) -> Result<Job, Error> {
        validate_uuid(model_id, "model_id")?;
        let mut body = json!({
            "model_id": model_id,
            "inputs": inputs,
            "output_delivery": "auto"
        });
        if let Some(units) = compute_units {
            body["compute_units"] = serde_json::to_value(units).expect("enum serializes");
        }
        self.request(Method::POST, "/coreml/predictions", Some(body))
            .await
    }

    pub async fn release_coreml(&self, model_id: &str) -> Result<bool, Error> {
        let response: ReleaseResponse = self
            .request(
                Method::DELETE,
                &format!("/coreml/models/{}", validate_uuid(model_id, "model_id")?),
                None,
            )
            .await?;
        Ok(response.released)
    }

    pub async fn compile_metal_inline(&self, source: &str) -> Result<Job, Error> {
        self.request(
            Method::POST,
            "/metal/libraries",
            Some(json!({ "source": { "storage": "inline", "text": source }, "fast_math": true })),
        )
        .await
    }

    pub async fn compile_metal_scratch(&self, source: &ScratchReference) -> Result<Job, Error> {
        source.validate()?;
        self.request(
            Method::POST,
            "/metal/libraries",
            Some(
                json!({ "source": { "storage": "scratch", "object": source }, "fast_math": true }),
            ),
        )
        .await
    }

    pub async fn dispatch_metal(&self, request: MetalDispatch<'_>) -> Result<Job, Error> {
        validate_uuid(request.library_id, "library_id")?;
        self.request(
            Method::POST,
            "/metal/dispatches",
            Some(serde_json::to_value(request).expect("dispatch serializes")),
        )
        .await
    }

    pub async fn release_metal(&self, library_id: &str) -> Result<bool, Error> {
        let response: ReleaseResponse = self
            .request(
                Method::DELETE,
                &format!(
                    "/metal/libraries/{}",
                    validate_uuid(library_id, "library_id")?
                ),
                None,
            )
            .await?;
        Ok(response.released)
    }

    pub async fn job(&self, job_id: &str) -> Result<Job, Error> {
        self.request(
            Method::GET,
            &format!("/jobs/{}", validate_uuid(job_id, "job_id")?),
            None,
        )
        .await
    }

    pub async fn cancel(&self, job_id: &str) -> Result<Job, Error> {
        self.request(
            Method::DELETE,
            &format!("/jobs/{}", validate_uuid(job_id, "job_id")?),
            None,
        )
        .await
    }

    pub async fn events(
        &self,
        job_id: &str,
        after: u64,
        wait_seconds: f32,
    ) -> Result<JobEventPage, Error> {
        if !(0.0..=30.0).contains(&wait_seconds) {
            return Err(Error::InvalidInput(
                "wait_seconds must be between zero and thirty".into(),
            ));
        }
        self.request(
            Method::GET,
            &format!(
                "/jobs/{}/events?after={after}&wait_seconds={wait_seconds}",
                validate_uuid(job_id, "job_id")?
            ),
            None,
        )
        .await
    }

    async fn request<T: DeserializeOwned>(
        &self,
        method: Method,
        path: &str,
        body: Option<Value>,
    ) -> Result<T, Error> {
        let mut request = self
            .http
            .request(method, format!("{}{path}", self.endpoint))
            .bearer_auth(&self.token)
            .header("X-Forge-Protocol-Version", PROTOCOL_VERSION)
            .header("Accept", "application/json, application/problem+json")
            .header("User-Agent", "forge-accelerator-rust/1.0");
        if let Some(body) = body {
            request = request.json(&body);
        }
        let response = request.send().await?;
        let status = response.status();
        if status.is_success() {
            return Ok(response.json().await?);
        }
        let envelope: ErrorEnvelope = response.json().await.map_err(|error| {
            Error::Protocol(format!(
                "HTTP {status} returned invalid error JSON: {error}"
            ))
        })?;
        Err(Error::Bridge {
            status,
            body: envelope.error,
        })
    }
}

#[derive(Serialize)]
pub struct MetalDispatch<'a> {
    pub library_id: &'a str,
    pub function: &'a str,
    pub grid: [u32; 3],
    pub threadgroup: [u32; 3],
    pub buffers: &'a [MetalBuffer],
    pub constants: &'a BTreeMap<String, Value>,
    pub output_delivery: &'a str,
}

#[derive(Clone, Debug, Serialize)]
pub struct MetalBuffer {
    pub index: u8,
    pub access: BufferAccess,
    pub tensor: Tensor,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BufferAccess {
    Read,
    Write,
    ReadWrite,
}

#[derive(Deserialize)]
struct ErrorEnvelope {
    error: BridgeErrorBody,
}

#[derive(Deserialize)]
struct ReleaseResponse {
    released: bool,
}

fn validate_uuid<'a>(value: &'a str, name: &str) -> Result<&'a str, Error> {
    let bytes = value.as_bytes();
    if bytes.len() != 36
        || ![8, 13, 18, 23]
            .into_iter()
            .all(|index| bytes[index] == b'-')
        || bytes
            .iter()
            .enumerate()
            .any(|(index, byte)| ![8, 13, 18, 23].contains(&index) && !byte.is_ascii_hexdigit())
    {
        return Err(Error::InvalidInput(format!("{name} must be a UUID")));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn hashes_scratch_file_and_rejects_escape() {
        let temporary = tempfile::tempdir().unwrap();
        let file = temporary.path().join("model.mlmodel");
        fs::write(&file, b"forge").unwrap();
        let reference = ScratchReference::from_file(&file, temporary.path()).unwrap();
        assert_eq!(reference.relative_path, "model.mlmodel");
        assert_eq!(reference.size, 5);
        assert_eq!(
            reference.sha256,
            "71b41d6dd48dc58eba8f5cf9edf30fef6597fdf285a521bb8fcbad4b3d50887d"
        );
        let other = tempfile::tempdir().unwrap();
        let outside = other.path().join("outside");
        fs::write(&outside, b"no").unwrap();
        assert!(ScratchReference::from_file(outside, temporary.path()).is_err());
    }

    #[test]
    fn refuses_short_token_and_other_authority() {
        assert!(Client::new("short").is_err());
        let token = "a".repeat(64);
        assert!(Client::with_endpoint(token, "http://127.0.0.1:4777/x", false).is_err());
    }
}
