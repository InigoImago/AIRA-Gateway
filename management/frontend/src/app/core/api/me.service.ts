import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Me } from './models';

@Injectable({ providedIn: 'root' })
export class MeService {
  private readonly http = inject(HttpClient);

  get(): Observable<Me> {
    return this.http.get<Me>('/api/v1/me');
  }
}
