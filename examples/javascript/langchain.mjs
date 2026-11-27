// LangChain.js's Gemini integration, @langchain/google-genai, through AIRA: chat, stream, embed.
//
//   AUTH=key node langchain.mjs   # or: bearer, service-account, token-as-key
//
// The base URL is the use case's address, <gateway>/uc/<use-case>. The chat model takes headers
// (customHeaders); the embedding model takes none, so a token goes in its key field.
import { ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings } from '@langchain/google-genai';

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

let apiKey; // the key field: an AIRA API key, or a token for a client without a header setting
let token; // a token sent as Authorization: Bearer
switch (env.AUTH ?? 'key') {
  case 'key': // an AIRA API key, issued in the console: use case -> API keys
    apiKey = env.API_KEY;
    break;
  case 'bearer': // a person's Keycloak access token
    token = env.ACCESS_TOKEN;
    break;
  case 'service-account': // a service account's token
    token = await serviceAccountToken();
    break;
  case 'token-as-key': // a token in the key's place
    apiKey = env.ACCESS_TOKEN || (await serviceAccountToken());
    break;
  default:
    throw new Error(`unknown AUTH ${env.AUTH}`);
}

const baseUrl = `${env.GATEWAY_URL}/uc/${env.USE_CASE}`;
const customHeaders = token ? { Authorization: `Bearer ${token}` } : {};
// The chat model insists on a key; a bearer header is read before it.
const llm = new ChatGoogleGenerativeAI({
  model: MODEL,
  apiKey: apiKey ?? 'unused',
  baseUrl,
  customHeaders,
});

console.log((await llm.invoke('What is an API gateway? One sentence.')).text);
for await (const chunk of await llm.stream('Count to five.')) process.stdout.write(chunk.text);
console.log();

// The embedding model has no header setting, so a token goes where its key would.
const embeddings = new GoogleGenerativeAIEmbeddings({
  model: EMBEDDING_MODEL,
  apiKey: apiKey ?? token,
  baseUrl,
});
const vector = await embeddings.embedQuery('How high is the Zugspitze?');
console.log(`embedding: ${vector.length} dimensions`);
