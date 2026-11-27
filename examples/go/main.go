// Google's Go SDK, google.golang.org/genai, through AIRA: one answer, a streamed one, an embedding.
//
//	AUTH=key go run .   # or: bearer, service-account, token-as-key
//
// The base URL is the use case's address, <gateway>/uc/<use-case>; the SDK appends
// /v1beta/models/<model>:generateContent. The credential is an AIRA API key or a Keycloak token.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"

	"google.golang.org/genai"
)

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

// serviceAccountToken is Keycloak's client-credentials grant: a confidential client signs in
// as itself.
func serviceAccountToken() string {
	form := url.Values{"grant_type": {"client_credentials"}}
	form.Set("client_id", os.Getenv("CLIENT_ID"))
	form.Set("client_secret", os.Getenv("CLIENT_SECRET"))
	response, err := http.PostForm(os.Getenv("TOKEN_URL"), form)
	if err != nil {
		log.Fatal(err)
	}
	defer response.Body.Close()
	var token struct {
		AccessToken string `json:"access_token"`
	}
	err = json.NewDecoder(response.Body).Decode(&token)
	if err != nil || response.StatusCode != http.StatusOK {
		log.Fatalf("token endpoint: %s", response.Status)
	}
	return token.AccessToken
}

func main() {
	ctx := context.Background()
	apiKey := "unused" // the SDK insists on a key; a bearer header is read before it
	headers := http.Header{}
	switch env("AUTH", "key") {
	case "key": // an AIRA API key, issued in the console: use case -> API keys
		apiKey = os.Getenv("API_KEY")
	case "bearer": // a person's Keycloak access token
		headers.Set("Authorization", "Bearer "+os.Getenv("ACCESS_TOKEN"))
	case "service-account": // a service account's token
		headers.Set("Authorization", "Bearer "+serviceAccountToken())
	case "token-as-key": // a token in the key's place, for clients without a header setting
		if apiKey = os.Getenv("ACCESS_TOKEN"); apiKey == "" {
			apiKey = serviceAccountToken()
		}
	default:
		log.Fatalf("unknown AUTH %q", os.Getenv("AUTH"))
	}

	client, err := genai.NewClient(ctx, &genai.ClientConfig{
		APIKey:  apiKey,
		Backend: genai.BackendGeminiAPI,
		HTTPOptions: genai.HTTPOptions{
			BaseURL: os.Getenv("GATEWAY_URL") + "/uc/" + os.Getenv("USE_CASE"),
			// The SDK's default is /v1; /v1beta is what Google's other SDKs call.
			APIVersion: "v1beta",
			Headers:    headers,
		},
	})
	if err != nil {
		log.Fatal(err)
	}
	model := env("MODEL", "gemini-2.5-flash")

	question := genai.Text("What is an API gateway?")
	answer, err := client.Models.GenerateContent(ctx, model, question, nil)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(answer.Text())

	count := genai.Text("Count to five.")
	for chunk, err := range client.Models.GenerateContentStream(ctx, model, count, nil) {
		if err != nil {
			log.Fatal(err)
		}
		fmt.Print(chunk.Text())
	}
	fmt.Println()

	embeddingModel := env("EMBEDDING_MODEL", "gemini-embedding-001")
	text := genai.Text("How high is the Zugspitze?")
	result, err := client.Models.EmbedContent(ctx, embeddingModel, text, nil)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("embedding: %d dimensions\n", len(result.Embeddings[0].Values))
}
