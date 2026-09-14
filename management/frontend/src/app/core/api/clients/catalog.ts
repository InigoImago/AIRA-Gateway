import { Observable, map } from 'rxjs';
import { API, GW } from '../prefixes';
import {
  CatalogModel,
  GatewayConfiguration,
  KiraModel,
  ModelCheck,
  OfferedModel,
  ServedModel,
  ThinkingLevelCheck,
} from '../types/catalog';
import { ApiClientClass, seg } from './base';

/**
 * Where a model lives, as the **form** currently says — not as the catalogue has it stored.
 *
 * Both checks take it because both are buttons inside the editor; without it they answered about
 * the saved row, i.e. about the declaration being replaced.
 */
export interface Provenance {
  provider?: string;
  publisher?: string;
  region?: string;
}

/** Only what was actually given: an empty string would override a stored value with nothing. */
function provenanceParams(where: Provenance): Record<string, string> {
  const params: Record<string, string> = {};
  for (const key of ['provider', 'publisher', 'region'] as const) {
    const value = where[key]?.trim();
    if (value) params[key] = value;
  }
  return params;
}

/**
 * The model catalogue (Management) and what the gateway knows about models: which it serves,
 * which providers it has, what they offer, and whether a model answers (`FRD-507`, `FRD-506`).
 */
export function withCatalog<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    /**
     * The **whole** catalog, deliberately unpaged (`FRD-208`).
     *
     * Tens of models, not thousands, and the screen's warnings count over all of it ("N have no
     * price on file") — a paged count would mean "N on this page".
     */
    models(): Observable<CatalogModel[]> {
      return this.http.get<CatalogModel[]>(`${API}/v1/models/`);
    }

    saveModel(model: CatalogModel): Observable<CatalogModel> {
      return this.http.post<CatalogModel>(`${API}/v1/models/`, model);
    }

    removeModel(name: string): Observable<void> {
      return this.http.delete<void>(`${API}/v1/models/${seg(name)}/`);
    }

    /**
     * The models as the compatibility surface addresses them, with their integer ids (`FRD-107`).
     *
     * Asked of the gateway: the catalog also holds `numeric_id`, but only `catalog.write` may read
     * it, and a use-case administrator setting a client up does not hold it.
     */
    kiraModels(): Observable<KiraModel[]> {
      return this.http.get<KiraModel[]>(`${GW}/kira/api/external/models`);
    }

    /** What the **gateway** serves, with the provenance its adapters were configured with
     *  (`FRD-507`) — a property of the gateway's configuration that nothing else knows. */
    servedModels(): Observable<ServedModel[]> {
      return this.http.get<{ models: ServedModel[] }>(`${GW}/v1beta/models`).pipe(
        map((body) =>
          (body.models ?? []).map((model) => ({
            ...model,
            // `models/` is Google's resource form, not a model name: carried across, it would
            // catalogue an entry no request can match. Stripped here, where the wire shape ends.
            name: model.name.replace(/^models\//, ''),
          })),
        ),
      );
    }

    /**
     * Which upstreams this gateway is configured with, and where it permits processing
     * (`FRD-507` stage C) — what *this installation* has, not what the product supports.
     */
    providers(): Observable<GatewayConfiguration> {
      return this.http.get<GatewayConfiguration>(`${GW}/v1beta/providers`).pipe(
        map((body) => ({
          providers: body.providers ?? [],
          // An older gateway sends no list. Absence means *it did not say*, never "nothing is
          // allowed", which would refuse every region a reader typed.
          allowedRegions: body.allowedRegions ?? [],
        })),
      );
    }

    /** What one provider offers this credential. Asked only when a provider is chosen: one key can
     *  list fifty models, and a round trip per upstream on load fills a dropdown nobody opened. */
    providerOfferings(provider: string): Observable<OfferedModel[]> {
      return this.http
        .get<{ models: OfferedModel[] }>(`${GW}/v1beta/providers/${seg(provider)}/offerings`)
        .pipe(map((body) => body.models ?? []));
    }

    /**
     * Is this model actually reachable, or only written down (`FRD-506`)?
     *
     * Never a generation: a self-deployed model can be scaled to zero, and a check must not be what
     * wakes it, bills for it, and takes minutes to answer.
     */
    checkModel(model: string, where: Provenance = {}): Observable<ModelCheck> {
      return this.http.get<ModelCheck>(`${GW}/v1beta/models/${seg(model)}:check`, {
        params: provenanceParams(where),
      });
    }

    /**
     * Does this model accept these level words, and can its dialect express these modes
     * (`ADR-0021`)?
     *
     * **This one does generate**, capped at one output token: `:countTokens` ignores
     * `generationConfig`, and a refused word costs nothing because the refusal precedes generation.
     * The modes cost no request at all — the dialect either has the field or it does not.
     */
    checkThinkingLevels(
      model: string,
      levels: string[],
      where: Provenance = {},
      modes: string[] = [],
    ): Observable<ThinkingLevelCheck> {
      return this.http.post<ThinkingLevelCheck>(
        `${GW}/v1beta/models/${seg(model)}:checkThinking`,
        { levels, modes },
        { params: provenanceParams(where) },
      );
    }
  };
}
