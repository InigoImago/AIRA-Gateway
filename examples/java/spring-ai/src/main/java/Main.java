// Spring AI's Google GenAI models through AIRA: one answer, a streamed one, an embedding.
//
//   AUTH=key mvn -q compile exec:java   # or: bearer, service-account, token-as-key
//
// Both models are built from Google's com.google.genai.Client, which is where the address
// (<gateway>/uc/<use-case>) and the credential go. No Spring Boot here, so nothing is hidden.
import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.genai.Client;
import com.google.genai.types.HttpOptions;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.google.genai.GoogleGenAiChatModel;
import org.springframework.ai.google.genai.GoogleGenAiChatOptions;
import org.springframework.ai.google.genai.embedding.GoogleGenAiEmbeddingConnectionDetails;
import org.springframework.ai.google.genai.text.GoogleGenAiTextEmbeddingModel;
import org.springframework.ai.google.genai.text.GoogleGenAiTextEmbeddingOptions;

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
    String apiKey = "unused"; // the SDK requires a key; where a bearer header is sent, it is read
    Map<String, String> headers = new HashMap<>();
    switch (env("AUTH", "key")) {
      // an AIRA API key, issued in the console: use case -> API keys
      case "key" -> apiKey = env("API_KEY");
      // a person's Keycloak access token
      case "bearer" -> headers.put("Authorization", "Bearer " + env("ACCESS_TOKEN"));
      // a service account's token
      case "service-account" -> headers.put("Authorization", "Bearer " + serviceAccountToken());
      // a token in the key's place, for clients without a header setting
      case "token-as-key" -> {
        String person = env("ACCESS_TOKEN", null);
        apiKey = person != null ? person : serviceAccountToken();
      }
      default -> throw new IllegalArgumentException("unknown AUTH " + env("AUTH"));
    }
    HttpOptions http = HttpOptions.builder()
        .baseUrl(env("GATEWAY_URL") + "/uc/" + env("USE_CASE"))
        .apiVersion("v1beta")
        .headers(headers)
        .build();

    try (Client genai = Client.builder().apiKey(apiKey).httpOptions(http).build()) {
      String model = env("MODEL", "gemini-2.5-flash");
      var options = GoogleGenAiChatOptions.builder().model(model).build();
      var chatModel = GoogleGenAiChatModel.builder().genAiClient(genai).options(options).build();
      ChatClient chat = ChatClient.create(chatModel);
      System.out.println(chat.prompt("What is an API gateway?").call().content());
      var stream = chat.prompt("Count to five.").stream().content();
      stream.doOnNext(System.out::print).blockLast();
      System.out.println();

      var connection = GoogleGenAiEmbeddingConnectionDetails.builder().genAiClient(genai).build();
      var embeddingOptions = GoogleGenAiTextEmbeddingOptions.builder()
          .model(env("EMBEDDING_MODEL", "gemini-embedding-001"))
          .build();
      var embeddings = new GoogleGenAiTextEmbeddingModel(connection, embeddingOptions);
      int dimensions = embeddings.embed("How high is the Zugspitze?").length;
      System.out.println("embedding: " + dimensions + " dimensions");
    }
  }
}
