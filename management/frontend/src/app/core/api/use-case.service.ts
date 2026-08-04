import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Membership, UseCase } from './models';

@Injectable({ providedIn: 'root' })
export class UseCaseService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/v1/use-cases/';

  list(): Observable<UseCase[]> {
    return this.http.get<UseCase[]>(this.base);
  }

  get(slug: string): Observable<UseCase> {
    return this.http.get<UseCase>(`${this.base}${slug}/`);
  }

  create(useCase: Partial<UseCase>): Observable<UseCase> {
    return this.http.post<UseCase>(this.base, useCase);
  }

  update(slug: string, changes: Partial<UseCase>): Observable<UseCase> {
    return this.http.patch<UseCase>(`${this.base}${slug}/`, changes);
  }

  remove(slug: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${slug}/`);
  }

  members(slug: string): Observable<Membership[]> {
    return this.http.get<Membership[]>(`${this.base}${slug}/members/`);
  }

  addMember(slug: string, username: string, role: string): Observable<Membership> {
    return this.http.post<Membership>(`${this.base}${slug}/members/`, { username, role });
  }

  removeMember(slug: string, username: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${slug}/members/${username}/`);
  }
}
