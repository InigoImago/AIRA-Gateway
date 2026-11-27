// Google's JavaScript SDK, @google/genai, through AIRA: one answer, a streamed one, an embedding.
//
//   AUTH=key node google-genai.mjs   # or: bearer, service-account, token-as-key
//
// The base URL is the use case's address, <gateway>/uc/<use-case>; the SDK appends
// /v1beta/models/<model>:generateContent. The credential is an AIRA API key or a Keycloak token.
import { GoogleGenAI } from '@google/genai';

const env = process.env;
const MODEL = env.MODEL ?? 'gemini-2.5-flash';
const EMBEDDING_MODEL = env.EMBEDDING_MODEL ?? 'gemini-embedding-001';

// Keycloak's client-credentials grant: a confidential client signs in as itself.
async function serviceAccountToken() {
  const form = new URLSearchParams({ grant_type: 'client_credentials' });
  form.set('client_id', env.CLIENT_ID);
  form.set('client_secret', env.CLIENT_SECRET);
  const response = await fetch(env.TOKEN_URL, { method: 'POST', body: form });
  if (!response.ok) throw new Error(`token endpoint: ${response.status} ${await response.text()}`);
  return (await response.json()).access_token;
}

let apiKey = 'unused'; // the SDK insists on a key; a bearer header is read before it
const headers = {};
switch (env.AUTH ?? 'key') {
  case 'key': // an AIRA API key, issued in the console: use case -> API keys
    apiKey = env.API_KEY;
    break;
  case 'bearer': // a person's Keycloak access token
    headers.Authorization = `Bearer ${env.ACCESS_TOKEN}`;
    break;
  case 'service-account': // a service account's token
    headers.Authorization = `Bearer ${await serviceAccountToken()}`;
    break;
  case 'token-as-key': // a token in the key's place, for clients without a header setting
    apiKey = env.ACCESS_TOKEN || (await serviceAccountToken());
    break;
  default:
    throw new Error(`unknown AUTH ${env.AUTH}`);
}

// The SDK's default is /v1; /v1beta is what Google's other SDKs call, and every AIRA serves it.
const baseUrl = `${env.GATEWAY_URL}/uc/${env.USE_CASE}`;
const ai = new GoogleGenAI({ apiKey, httpOptions: { baseUrl, apiVersion: 'v1beta', headers } });

const answer = await ai.models.generateContent({
  model: MODEL,
  contents: 'What is an API gateway?',
});
console.log(answer.text);

const stream = await ai.models.generateContentStream({ model: MODEL, contents: 'Count to five.' });
for await (const chunk of stream) process.stdout.write(chunk.text ?? '');
console.log();

const text = 'How high is the Zugspitze?';
const { embeddings } = await ai.models.embedContent({ model: EMBEDDING_MODEL, contents: text });
console.log(`embedding: ${embeddings[0].values.length} dimensions`);
