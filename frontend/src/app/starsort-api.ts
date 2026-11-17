import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

// Same origin as the site: CloudFront sends /api/* to API Gateway, so the session cookie goes along.
export const LOGIN_URL = '/api/auth/login';

export type TrialReason = 'budget' | 'running' | 'weekly';

export interface TrialStatus {
  unlimited: boolean;
  can_run: boolean;
  reason: TrialReason | null;
  retry_at: number | null; // Unix seconds
  message: string | null;
}

export interface Me {
  login: string;
  id: number;
  name: string | null;
  avatar_url: string;
  trial: TrialStatus;
}

@Injectable({ providedIn: 'root' })
export class StarsortApi {
  private readonly http = inject(HttpClient);

  me(): Observable<Me> {
    return this.http.get<Me>('/api/me');
  }

  logout(): Observable<unknown> {
    return this.http.post('/api/auth/logout', null);
  }
}
