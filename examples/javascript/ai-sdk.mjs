// The Vercel AI SDK's Google provider, @ai-sdk/google, through AIRA: text, a stream, an embedding.
//
//   AUTH=key node ai-sdk.mjs   # or: bearer, service-account, token-as-key
//
// Unlike Google's SDKs, the provider's baseURL includes the API version:
// <gateway>/uc/<use-case>/v1beta.
import { createGoogleGenerativeAI } from '@ai-sdk/google';
import { embed, generateText, streamText } from 'ai';

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

let apiKey = 'unused'; // the provider insists on a key; a bearer header is read before it
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

const baseURL = `${env.GATEWAY_URL}/uc/${env.USE_CASE}/v1beta`;
const google = createGoogleGenerativeAI({ apiKey, baseURL, headers });

const { text } = await generateText({ model: google(MODEL), prompt: 'What is an API gateway?' });
console.log(text);

const stream = streamText({ model: google(MODEL), prompt: 'Count to five.' });
for await (const delta of stream.textStream) process.stdout.write(delta);
console.log();

const value = 'How high is the Zugspitze?';
const { embedding } = await embed({ model: google.embedding(EMBEDDING_MODEL), value });
console.log(`embedding: ${embedding.length} dimensions`);
