<?php
// google-gemini-php/client through AIRA: one answer, a streamed one, an embedding.
//
//   composer install && AUTH=key php chat.php   # or: bearer, service-account, token-as-key
//
// The client's base URL includes the API version and ends in a slash:
// <gateway>/uc/<use-case>/v1beta/. A header goes in withHttpHeader().
require __DIR__ . '/vendor/autoload.php';

function env(string $name, ?string $fallback = null): string
{
    $value = getenv($name);
    if ($value !== false && $value !== '') {
        return $value;
    }
    return $fallback ?? throw new RuntimeException("$name is not set");
}

// Keycloak's client-credentials grant: a confidential client signs in as itself.
function serviceAccountToken(): string
{
    $response = (new GuzzleHttp\Client())->post(env('TOKEN_URL'), ['form_params' => [
        'grant_type' => 'client_credentials',
        'client_id' => env('CLIENT_ID'),
        'client_secret' => env('CLIENT_SECRET'),
    ]]);
    return json_decode((string) $response->getBody(), true)['access_token'];
}

$factory = Gemini::factory()->withBaseUrl(env('GATEWAY_URL') . '/uc/' . env('USE_CASE') . '/v1beta/');
$factory = match (env('AUTH', 'key')) {
    // an AIRA API key, issued in the console: use case -> API keys
    'key' => $factory->withApiKey(env('API_KEY')),
    // a person's Keycloak access token
    'bearer' => $factory->withHttpHeader('Authorization', 'Bearer ' . env('ACCESS_TOKEN')),
    // a service account's token
    'service-account' => $factory->withHttpHeader('Authorization', 'Bearer ' . serviceAccountToken()),
    // a token in the key's place, for clients without a header setting
    'token-as-key' => $factory->withApiKey(env('ACCESS_TOKEN', '') ?: serviceAccountToken()),
    default => throw new InvalidArgumentException('unknown AUTH ' . env('AUTH')),
};
$client = $factory->make();
$model = env('MODEL', 'gemini-2.5-flash');

echo $client->generativeModel(model: $model)->generateContent('What is an API gateway?')->text(), "\n";

foreach ($client->generativeModel(model: $model)->streamGenerateContent('Count to five.') as $chunk) {
    echo $chunk->text();
}
echo "\n";

$embeddingModel = $client->embeddingModel(env('EMBEDDING_MODEL', 'gemini-embedding-001'));
$embedding = $embeddingModel->embedContent('How high is the Zugspitze?');
echo 'embedding: ', count($embedding->embedding->values), " dimensions\n";
