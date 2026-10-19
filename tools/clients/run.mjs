// One real Gemini client against the gateway: the AI SDK's Google provider in one of its majors,
// with the `ai` major it ships for. Written the way an application writes it, so that what reaches
// the gateway is what that client sends — not what a test of the gateway thinks it sends.
//
//   node run.mjs <major 1-4> <base URL ending in /v1beta> <use case>
//
// Prints one JSON line: what each scenario returned, or the error it raised. The caller —
// gateway/tests/test_real_gemini_clients.py — decides what is right.

const [, , major, baseURL, useCase] = process.argv;
const AI_FOR_PROVIDER = { 1: 4, 2: 5, 3: 6, 4: 7 };
const ai = await import(`ai-v${AI_FOR_PROVIDER[major]}`);
const { createGoogleGenerativeAI } = await import(`google-v${major}`);

const google = createGoogleGenerativeAI({
  baseURL,
  apiKey: 'not-checked-here',
  headers: { 'x-aira-use-case': useCase },
});
const model = google('mock-1');

// JSON Schema written as a zod schema would be: draft-07 keywords the OpenAPI subset lacks.
const readFileSchema = ai.jsonSchema({
  type: 'object',
  properties: {
    path: { type: 'string', description: 'the file to read' },
    limit: { type: 'integer', exclusiveMinimum: 0 },
  },
  required: ['path'],
  additionalProperties: false,
});
const documentSchema = ai.jsonSchema({
  type: 'object',
  properties: { title: { type: 'string' }, pages: { type: 'integer' } },
  required: ['title', 'pages'],
  additionalProperties: false,
});

const executed = [];
const readFile = ai.tool({
  description: 'Read a file.',
  // The input key was renamed between the fourth and fifth majors of `ai`.
  ...(AI_FOR_PROVIDER[major] === 4 ? { parameters: readFileSchema } : { inputSchema: readFileSchema }),
  execute: async (input) => {
    executed.push(input);
    return { content: 'print("hello")' };
  },
});
const twoSteps =
  AI_FOR_PROVIDER[major] === 4 ? { maxSteps: 2 } : { stopWhen: ai.stepCountIs(2) };

async function scenario(name, run) {
  try {
    return [name, { ok: true, ...(await run()) }];
  } catch (error) {
    return [name, { ok: false, error: String(error?.message ?? error).slice(0, 400) }];
  }
}

// One after the other: the gateway the test serves keeps its audit trail in SQLite, which refuses a
// commit while another request's statement is open — a property of the test's database, not of
// the gateway, and not what this measures.
const results = {};
for (const [name, run] of [
  [
    'tool',
    async () => {
      const result = await ai.generateText({
        model,
        prompt: 'read hello.py',
        tools: { read_file: readFile },
        ...twoSteps,
      });
      return { steps: result.steps.length, executed, text: result.text };
    },
  ],
  [
    'object',
    async () => {
      const result = await ai.generateObject({
        model,
        prompt: 'describe a book',
        schema: documentSchema,
      });
      return { object: result.object };
    },
  ],
  [
    'stream',
    async () => {
      const result = ai.streamText({ model, prompt: 'say hello' });
      let text = '';
      for await (const delta of result.textStream) text += delta;
      return { text };
    },
  ],
]) {
  const [key, outcome] = await scenario(name, run);
  results[key] = outcome;
}

console.log(JSON.stringify(results));
