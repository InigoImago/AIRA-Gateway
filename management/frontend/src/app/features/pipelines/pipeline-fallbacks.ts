import { Component, computed, input, output } from '@angular/core';
import { CatalogModel, FALLBACK_KINDS, FallbackKind } from '../../core/api/models';
import { MultiSelect, MultiSelectOption } from '../../core/ui/multi-select';
import { KIND_LABELS, canServe } from './fallback-kinds';

/** One kind's chain as the form holds it. */
export type FallbackChains = Record<FallbackKind, string[]>;

/** What a model offered for a kind cannot do, said on the chip of one the chain still names. */
const CANNOT: Record<FallbackKind, string> = {
  generate: 'writes no text — skipped',
  embed: 'gives no embeddings — skipped',
  rerank: 'is no reranker — skipped',
};

/** Why a picker has nothing to offer, once the release is known. */
const NOTHING_RELEASED: Record<FallbackKind, string> = {
  generate: 'No model released to this use case writes text.',
  embed: 'No model released to this use case gives embeddings.',
  rerank: 'No model released to this use case ranks records.',
};

/**
 * What answers when a model cannot (`FRD-310`): the use case's fallback chain for each kind of
 * request — text generation, embedding, ranking — in the pipeline inspector.
 *
 * A panel of the pipeline editor, which owns the pipeline and saves it whole: this one only offers
 * the choices and hands each change back (`changed`). Per kind it offers the models released to
 * the use case that can serve that kind, so an embedding never falls back to a model that writes
 * text; a model the chain names that is no longer one of those stays on its chip, marked, so it
 * can be taken out where it is read rather than vanishing from the list. The server refuses the
 * rest by name.
 */
@Component({
  selector: 'app-pipeline-fallbacks',
  imports: [MultiSelect],
  templateUrl: './pipeline-fallbacks.html',
  styles: `
    /* The inspector's own rhythm: the panel is one child of it, and its fields would touch. */
    :host {
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
      min-width: 0;
    }
    /* The sentence under the title sits as close to it as a step's does in the same inspector: a
       flex gap does not collapse margins, so the paragraph's own top margin would double it. */
    .fallbacks__lead {
      margin-top: -0.4rem;
    }
  `,
})
export class PipelineFallbacks {
  /** The chain each kind of request falls back along, as the form holds it. */
  readonly chains = input.required<FallbackChains>();
  /** The models released to this use case (`FRD-308`). */
  readonly released = input<string[]>([]);
  /** The catalogue, for what each released model can do; `null` until it answers, and then every
   *  released model is offered for every kind and the server refuses what cannot serve it. */
  readonly catalogue = input<CatalogModel[] | null>(null);
  /** Whether the release has been read: before it, nothing is said about what is not released. */
  readonly releaseKnown = input(false);
  readonly canManage = input(false);
  readonly changed = output<{ kind: FallbackKind; models: string[] }>();

  protected readonly kinds = FALLBACK_KINDS;
  protected readonly label = (kind: FallbackKind): string => KIND_LABELS[kind];

  /** Per kind, what the picker offers: the released models that can serve it, then every model
   *  the chain names that is not one of those, marked with the reason the gateway skips it. */
  protected readonly choices = computed(() => {
    const catalogue = this.catalogue();
    const known = new Map((catalogue ?? []).map((model) => [model.name, model]));
    const released = this.released();
    const choices = {} as Record<FallbackKind, MultiSelectOption[]>;
    for (const kind of this.kinds) {
      const serves = (name: string): boolean => {
        const model = known.get(name);
        return !catalogue || !model || canServe(model, kind);
      };
      const offered: MultiSelectOption[] = released
        .filter(serves)
        .map((name) => ({ value: name, label: name }));
      for (const name of this.chains()[kind]) {
        if (offered.some((option) => option.value === name)) continue;
        const warning = !this.releaseKnown()
          ? undefined
          : !released.includes(name)
            ? 'not released — skipped'
            : CANNOT[kind];
        offered.push({ value: name, label: name, warning });
      }
      choices[kind] = offered;
    }
    return choices;
  });

  /**
   * What an empty picker says. Opened by somebody who may change the pipeline — which is known
   * only once the use case, and so its release, has answered — it says why nothing is offered. A
   * reader's picker is greyed and shows this as its value, where only "empty" is true.
   */
  protected emptyHint(kind: FallbackKind): string {
    return this.canManage() ? NOTHING_RELEASED[kind] : 'None';
  }
}
