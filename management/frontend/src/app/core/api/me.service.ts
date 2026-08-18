import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Me } from './models';
import { API } from './prefixes';

@Injectable({ providedIn: 'root' })
export class MeService {
  private readonly http = inject(HttpClient);

  get(): Observable<Me> {
    return this.http.get<Me>(`${API}/v1/me`);
  }
}
