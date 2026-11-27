// LangChain4j's Google AI Gemini module through AIRA: one answer, a streamed one, an embedding.
//
//   AUTH=key mvn -q compile exec:java   # or: bearer, service-account, token-as-key
//
// LangChain4j's base URL includes the API version: <gateway>/uc/<use-case>/v1beta. The chat
// models take headers (customHeaders); the embedding model takes none, so a token goes in its key.
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.model.chat.response.ChatResponse;
import dev.langchain4j.model.chat.response.StreamingChatResponseHandler;
import dev.langchain4j.model.googleai.GoogleAiEmbeddingModel;
import dev.langchain4j.model.googleai.GoogleAiGeminiChatModel;
import dev.langchain4j.model.googleai.GoogleAiGeminiStreamingChatModel;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

public class Main {
  static String env(String name, String fallback) {
    String value = System.getenv(name);
    return value == null || value.isEmpty() ? fallback : value;
  }

  static String env(String name) {
    return Objects.requireNonNull(env(name, null), name + " is not set");
  }

  /** Keycloak's client-credentials grant: a confidential client signs in as itself. */
  static String serviceAccountToken() throws Exception {
    String form = "grant_type=client_credentials"
        + "&client_id=" + URLEncoder.encode(env("CLIENT_ID"), StandardCharsets.UTF_8)
        + "&client_secret=" + URLEncoder.encode(env("CLIENT_SECRET"), StandardCharsets.UTF_8);
    HttpRequest request = HttpRequest.newBuilder(URI.create(env("TOKEN_URL")))
        .header("Content-Type", "application/x-www-form-urlencoded")
        .POST(HttpRequest.BodyPublishers.ofString(form))
        .build();
    HttpResponse<String> response =
        HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString());
    if (response.statusCode() != 200) {
      throw new IllegalStateException(
          "token endpoint: " + response.statusCode() + " " + response.body());
    }
    return new ObjectMapper().readTree(response.body()).get("access_token").asText();
  }

  public static void main(String[] args) throws Exception {
    String apiKey = null; // the key field: an AIRA API key, or a token for a client without headers
    String token = null; // a token sent as Authorization: Bearer
    switch (env("AUTH", "key")) {
      // an AIRA API key, issued in the console: use case -> API keys
      case "key" -> apiKey = env("API_KEY");
      // a person's Keycloak access token
      case "bearer" -> token = env("ACCESS_TOKEN");
      // a service account's token
      case "service-account" -> token = serviceAccountToken();
      // a token in the key's place
      case "token-as-key" -> {
        String person = env("ACCESS_TOKEN", null);
        apiKey = person != null ? person : serviceAccountToken();
      }
      default -> throw new IllegalArgumentException("unknown AUTH " + env("AUTH"));
    }
    String baseUrl = env("GATEWAY_URL") + "/uc/" + env("USE_CASE") + "/v1beta";
    String model = env("MODEL", "gemini-2.5-flash");
    Map<String, String> headers =
        token == null ? Map.of() : Map.of("Authorization", "Bearer " + token);
    String chatKey = apiKey == null ? "unused" : apiKey; // required; a bearer header is read first

    var chat = GoogleAiGeminiChatModel.builder()
        .baseUrl(baseUrl).apiKey(chatKey).customHeaders(headers).modelName(model).build();
    System.out.println(chat.chat("What is an API gateway?"));

    var streaming = GoogleAiGeminiStreamingChatModel.builder()
        .baseUrl(baseUrl).apiKey(chatKey).customHeaders(headers).modelName(model).build();
    CompletableFuture<ChatResponse> done = new CompletableFuture<>();
    streaming.chat("Count to five.", new StreamingChatResponseHandler() {
      public void onPartialResponse(String text) { System.out.print(text); }
      public void onCompleteResponse(ChatResponse response) { done.complete(response); }
      public void onError(Throwable error) { done.completeExceptionally(error); }
    });
    done.get(2, TimeUnit.MINUTES);
    System.out.println();

    var embeddings = GoogleAiEmbeddingModel.builder()
        .baseUrl(baseUrl)
        .apiKey(apiKey == null ? token : apiKey)
        .modelName(env("EMBEDDING_MODEL", "gemini-embedding-001"))
        .build();
    int dimensions = embeddings.embed("How high is the Zugspitze?").content().dimension();
    System.out.println("embedding: " + dimensions + " dimensions");
  }
}
