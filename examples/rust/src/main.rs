//! gemini-rust through AIRA: one answer, a streamed one, an embedding.
//!
//!     AUTH=key cargo run   # or: bearer, service-account, token-as-key
//!
//! The client's base URL includes the API version and ends in a slash:
//! <gateway>/uc/<use-case>/v1beta/. A header goes on the reqwest client it is built with.
use futures::StreamExt;
use gemini_rust::{GeminiBuilder, Model};
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn var(name: &str) -> Result<String> {
    std::env::var(name).map_err(|_| format!("{name} is not set").into())
}

fn var_or(name: &str, fallback: &str) -> String {
    std::env::var(name).ok().filter(|value| !value.is_empty()).unwrap_or(fallback.to_string())
}

/// Keycloak's client-credentials grant: a confidential client signs in as itself.
async fn service_account_token() -> Result<String> {
    let form = [
        ("grant_type", "client_credentials".to_string()),
        ("client_id", var("CLIENT_ID")?),
        ("client_secret", var("CLIENT_SECRET")?),
    ];
    let response = reqwest::Client::new().post(var("TOKEN_URL")?).form(&form).send().await?;
    let token: serde_json::Value = response.error_for_status()?.json().await?;
    Ok(token["access_token"].as_str().ok_or("no access_token in the answer")?.to_string())
}

// gemini-rust marks `generate_content` deprecated in favour of Google's Interactions API, which
// AIRA does not serve; `generateContent` is the API it does.
#[allow(deprecated)]
#[tokio::main]
async fn main() -> Result<()> {
    let mut api_key = "unused".to_string(); // the client insists on a key; a bearer header wins
    let mut headers = HeaderMap::new();
    match var_or("AUTH", "key").as_str() {
        // an AIRA API key, issued in the console: use case -> API keys
        "key" => api_key = var("API_KEY")?,
        // a person's Keycloak access token
        "bearer" => {
            let bearer = format!("Bearer {}", var("ACCESS_TOKEN")?);
            headers.insert(AUTHORIZATION, HeaderValue::from_str(&bearer)?);
        }
        // a service account's token
        "service-account" => {
            let bearer = format!("Bearer {}", service_account_token().await?);
            headers.insert(AUTHORIZATION, HeaderValue::from_str(&bearer)?);
        }
        // a token in the key's place, for clients without a header setting
        "token-as-key" => match var_or("ACCESS_TOKEN", "") {
            person if !person.is_empty() => api_key = person,
            _ => api_key = service_account_token().await?,
        },
        other => return Err(format!("unknown AUTH {other:?}").into()),
    }
    let base_url: reqwest::Url =
        format!("{}/uc/{}/v1beta/", var("GATEWAY_URL")?, var("USE_CASE")?).parse()?;
    let client = |model: String| {
        GeminiBuilder::new(api_key.clone())
            .with_model(Model::Custom(format!("models/{model}")))
            .with_base_url(base_url.clone())
            .with_http_client(reqwest::Client::builder().default_headers(headers.clone()))
            .build()
    };

    let chat = client(var_or("MODEL", "gemini-2.5-flash"))?;
    let question = chat.generate_content().with_user_message("What is an API gateway?");
    let answer = question.execute().await?;
    println!("{}", answer.text());

    let count = chat.generate_content().with_user_message("Count to five.");
    let mut stream = count.execute_stream().await?;
    while let Some(chunk) = stream.next().await {
        print!("{}", chunk?.text());
    }
    println!();

    let embedder = client(var_or("EMBEDDING_MODEL", "gemini-embedding-001"))?;
    let text = embedder.embed_content().with_text("How high is the Zugspitze?");
    let embedding = text.execute().await?;
    println!("embedding: {} dimensions", embedding.embedding.values.len());
    Ok(())
}
