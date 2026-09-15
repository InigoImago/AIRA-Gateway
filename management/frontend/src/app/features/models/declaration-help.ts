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
      served normally rather than skipped. Needs the two cache prices above to show up in
      reporting.`,
  speech: `The model answers with speech: text in, audio out. A speech model usually declares this
      and **not** generate, so a text request to it is refused by name rather than sent to a
      provider that refuses it without saying why. A model without it is skipped when a caller asks
      for audio. The audit trail keeps a description and a hash of the audio, not the audio.`,
};

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
