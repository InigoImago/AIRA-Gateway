import { Capability } from '../../core/api/models';

/**
 * What ticking each capability commits the platform to (`FRD-114`, `ADR-0012`).
 *
 * The vocabulary means *whether*, never *how*, but the consequences differ sharply: most
 * capabilities **exclude a model from a fallback chain** when absent, while `prompt_caching` only
 * changes the price. A checkbox label cannot say that, so the info hint beside it does.
 */
export const CAPABILITY_HELP: Record<Capability, string> = {
  generate: `The model answers prompts. Almost every model has this; a model without it is an
      embedding model, and the gateway refuses generation requests for it by name.`,
  embed: `The model turns text into vectors. Separate from generation because most models do one
      or the other, and a request to the wrong kind is refused rather than approximated.`,
  structured_output: `The model can be asked to answer as a document matching a schema. Three
      vendors do this by three unrelated mechanisms and the catalog never learns which — this says
      only that it works. A model without it is skipped when a caller submits a schema, because
      prose where a document was expected is a wrong answer that looks like a right one.`,
  thinking: `The model can reason before answering. Declare the modes and budgets it really
      supports, measured — a mode filled in from the list rather than from a test produces requests
      the provider refuses by name. Leave it off if you have not checked: no thinking is the safe
      declaration.`,
  attachments: `The model reads documents and images, not only text. The one capability where
      being wrong is worst: a model that cannot read the attachment is skipped, never sent the
      prompt without it — a dropped attachment produces a confident wrong answer with a 200, and
      the caller blames the model.`,
  tools: `The model can be given function definitions and answer by asking for one. The gateway
      carries the call and never runs it. A model without this is skipped when a caller declares
      functions, because a model that answers in prose instead breaks an assistant silently.`,
  prompt_caching: `The provider will honour a cache marker on this model's stable prefix, so a
      repeated tool declaration and system prompt cost a fraction on the next request. The one
      capability here that changes the **price** and not the answer — so a model without it is
      served normally rather than skipped. The saving shows up in reporting only once the two
      cache prices on the "What it costs" tab are set.`,
  speech: `The model answers with speech: text in, audio out. A speech model usually declares this
      and **not** generate, so a text request to it is refused by name rather than sent to a
      provider that refuses it without saying why. A model without it is skipped when a caller asks
      for audio. The audit trail keeps a description and a hash of the audio, not the audio.`,
  rerank: `The model orders records by how well each answers a query and scores each — the second
      step of a search, after the candidates are found. A reranker declares this alone, and a
      ranking request to any other model is refused by name. Its input figure is counted in ranking
      queries of up to 100 records, the unit the provider bills, so its input price is per million
      queries.`,
};

/**
 * What "fixed sampling" commits the platform to. The control is one checkbox, and what it costs a
 * caller — a refusal or another model — cannot fit in its label.
 */
export const FIXED_SAMPLING_HELP = `The model chooses how it samples and accepts no setting for it:
  a temperature other than 1, a top_p below 1, or any top_k is refused by the provider. With this
  ticked, the gateway never sends such a request to the model — it goes to the next model in the
  chain, or is refused with a message naming the setting. A request that leaves sampling alone, or
  sets the neutral values, is served as usual. The model reference fills this in where its list
  says so; leave it off for a model that accepts these settings.`;

/** What the sampling defaults commit the platform to (`FRD-146`). */
export const SAMPLING_DEFAULTS_HELP = `Sent with a request that does not set the control itself — a
  caller's own value always wins. Leave both empty and nothing is sent on anybody's behalf: the
  model uses its own defaults. Set them where an application was tuned against fixed values, for
  example top_p 0.8 and top_k 40. A model with fixed sampling takes none, and a provider whose
  interface has no top_k is simply not sent one.`;

/**
 * What a thinking mode means, for somebody configuring one.
 *
 * Every box declares an **instruction this model accepts**, not a state of the model — `disabled`
 * does not turn a reasoning model into an instruct model.
 */
export function thinkingModeMeans(mode: string): string {
  switch (mode) {
    case 'disabled':
      return 'can be told not to think at all';
    case 'auto':
      return 'can be left to decide for itself how much to think';
    case 'limited':
      return 'accepts a token budget the caller names, bounded below';
    default:
      return mode;
  }
}
