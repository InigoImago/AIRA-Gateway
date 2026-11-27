// Google's REST API from C#, with HttpClient and System.Text.Json: no package to install.
//
//   AUTH=key dotnet run   # or: query-key, bearer, service-account, token-as-key
//
// The address is the use case's, <gateway>/uc/<use-case>/v1beta. The gateway reads
// `Authorization: Bearer` first, then `x-goog-api-key`, then `?key=`, and accepts a Keycloak token
// wherever it accepts a key.
using System.Net.Http.Json;
using System.Text.Json.Nodes;

string Env(string name, string? fallback = null) =>
    Environment.GetEnvironmentVariable(name) is { Length: > 0 } value
        ? value
        : fallback ?? throw new InvalidOperationException($"{name} is not set");

// Keycloak's client-credentials grant: a confidential client signs in as itself.
async Task<string> ServiceAccountToken()
{
    using var keycloak = new HttpClient();
    var form = new FormUrlEncodedContent(new Dictionary<string, string>
    {
        ["grant_type"] = "client_credentials",
        ["client_id"] = Env("CLIENT_ID"),
        ["client_secret"] = Env("CLIENT_SECRET"),
    });
    var response = await keycloak.PostAsync(Env("TOKEN_URL"), form);
    response.EnsureSuccessStatusCode();
    var token = await response.Content.ReadFromJsonAsync<JsonObject>();
    return token!["access_token"]!.GetValue<string>();
}

var baseAddress = new Uri($"{Env("GATEWAY_URL")}/uc/{Env("USE_CASE")}/v1beta/");
using var client = new HttpClient { BaseAddress = baseAddress, Timeout = TimeSpan.FromMinutes(2) };
string? keyInUrl = null;
switch (Env("AUTH", "key"))
{
    case "key": // an AIRA API key, issued in the console: use case -> API keys
        client.DefaultRequestHeaders.Add("x-goog-api-key", Env("API_KEY"));
        break;
    case "query-key": // Google's curl form; every proxy on the way logs the URL, key included
        keyInUrl = Env("API_KEY");
        break;
    case "bearer": // a person's Keycloak access token
        client.DefaultRequestHeaders.Authorization = new("Bearer", Env("ACCESS_TOKEN"));
        break;
    case "service-account": // a service account's token
        client.DefaultRequestHeaders.Authorization = new("Bearer", await ServiceAccountToken());
        break;
    case "token-as-key": // a token in the key's place
        var person = Env("ACCESS_TOKEN", "");
        var token = person != "" ? person : await ServiceAccountToken();
        client.DefaultRequestHeaders.Add("x-goog-api-key", token);
        break;
    default:
        throw new ArgumentException($"unknown AUTH {Env("AUTH")}");
}

string Url(string path) => keyInUrl is null
    ? path
    : $"{path}{(path.Contains('?') ? '&' : '?')}key={Uri.EscapeDataString(keyInUrl)}";

async Task<JsonNode> Post(string path, object body)
{
    var response = await client.PostAsJsonAsync(Url(path), body);
    var json = await response.Content.ReadFromJsonAsync<JsonNode>();
    // A refusal comes in Google's error envelope: {"error": {"code", "message", "status", …}}.
    if (!response.IsSuccessStatusCode)
        throw new HttpRequestException($"{(int)response.StatusCode}: {json?["error"]?["message"]}");
    return json!;
}

// The answer's text. Reasoning, where a request asked for it, comes in parts marked `thought`.
string Text(JsonNode response) => string.Concat(
    response["candidates"]![0]!["content"]!["parts"]!.AsArray()
        .Where(part => part?["thought"]?.GetValue<bool>() != true)
        .Select(part => part?["text"]?.GetValue<string>()));

var model = Env("MODEL", "gemini-2.5-flash");
var question = new { role = "user", parts = new[] { new { text = "What is an API gateway?" } } };
var ask = new { contents = new[] { question } };
Console.WriteLine(Text(await Post($"models/{model}:generateContent", ask)));

// `alt=sse` streams server-sent events; without it the stream is one JSON array.
var streamPath = Url($"models/{model}:streamGenerateContent?alt=sse");
using var streamed = new HttpRequestMessage(HttpMethod.Post, streamPath);
streamed.Content = JsonContent.Create(ask);
using var events = await client.SendAsync(streamed, HttpCompletionOption.ResponseHeadersRead);
events.EnsureSuccessStatusCode();
using var reader = new StreamReader(await events.Content.ReadAsStreamAsync());
while (await reader.ReadLineAsync() is { } line)
    if (line.StartsWith("data: ")) Console.Write(Text(JsonNode.Parse(line[6..])!));
Console.WriteLine();

var embeddingModel = Env("EMBEDDING_MODEL", "gemini-embedding-001");
var text = new { content = new { parts = new[] { new { text = "How high is the Zugspitze?" } } } };
var embedding = await Post($"models/{embeddingModel}:embedContent", text);
Console.WriteLine($"embedding: {embedding["embedding"]!["values"]!.AsArray().Count} dimensions");
