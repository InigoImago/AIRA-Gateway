import { Observable } from 'rxjs';
import { API } from '../prefixes';
import {
  AccessChange,
  ApiKey,
  DirectoryResults,
  GroupGrant,
  IssuedApiKey,
  Membership,
} from '../types/use-cases';
import { ApiClientClass, seg } from './base';

/** Who may use a use case: members, group grants (`FRD-209`) and API keys. */
export function withAccess<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    members(slug: string): Observable<Membership[]> {
      return this.http.get<Membership[]>(`${this.base}${seg(slug)}/members/`);
    }

    addMember(slug: string, username: string, role: string): Observable<Membership> {
      return this.http.post<Membership>(`${this.base}${seg(slug)}/members/`, { username, role });
    }

    /** Remove a member. The server also revokes the keys that rested on the access (`FRD-613`),
     *  and names them in the answer so the screen can say so. */
    removeMember(slug: string, username: string): Observable<AccessChange> {
      return this.http.delete<AccessChange>(`${this.base}${seg(slug)}/members/${seg(username)}/`);
    }

    groupGrants(slug: string): Observable<GroupGrant[]> {
      return this.http.get<GroupGrant[]>(`${this.base}${seg(slug)}/groups/`);
    }

    grantGroup(slug: string, groupPath: string, role: string): Observable<GroupGrant> {
      return this.http.post<GroupGrant>(`${this.base}${seg(slug)}/groups/`, {
        group_path: groupPath,
        role,
      });
    }

    /** Revoke a group grant. The path travels in the **query string**: a Keycloak group path
     *  contains slashes, which an encoded path segment breaks at two levels deep. */
    revokeGroup(slug: string, groupPath: string): Observable<AccessChange> {
      return this.http.delete<AccessChange>(`${this.base}${seg(slug)}/groups/revoke/`, {
        params: { group_path: groupPath },
      });
    }

    /** Search Keycloak for groups and people a grant could name (`FRD-209` §3). */
    directory(query: string): Observable<DirectoryResults> {
      return this.http.get<DirectoryResults>(`${API}/v1/directory/`, { params: { q: query } });
    }

    apiKeys(slug: string): Observable<ApiKey[]> {
      return this.http.get<ApiKey[]>(`${this.base}${seg(slug)}/api-keys/`);
    }

    /**
     * Issue a key. `expiresInDays` and `owner` are **omitted when absent** rather than sent as
     * null: a key with no end date is the default, and you own what you create unless you name
     * somebody else.
     */
    issueApiKey(
      slug: string,
      label: string,
      expiresInDays?: number | null,
      owner?: string | null,
    ): Observable<IssuedApiKey> {
      const body: { label: string; expires_in_days?: number; owner?: string } = { label };
      if (expiresInDays) {
        body.expires_in_days = expiresInDays;
      }
      if (owner) {
        body.owner = owner;
      }
      return this.http.post<IssuedApiKey>(`${this.base}${seg(slug)}/api-keys/`, body);
    }

    revokeApiKey(slug: string, prefix: string): Observable<void> {
      return this.http.delete<void>(`${this.base}${seg(slug)}/api-keys/${seg(prefix)}/`);
    }
  };
}
