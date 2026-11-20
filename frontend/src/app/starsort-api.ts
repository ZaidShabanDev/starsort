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

export type JobStatus = 'queued' | 'running' | 'done' | 'failed';

export interface Job {
  job_id: string;
  login: string;
  status: JobStatus;
  created_at: number; // Unix seconds
  updated_at: number;
  batches_done: number;
  batches_total: number;
  error?: string;
  // Only when status is 'done':
  markdown?: string;
  categories?: Record<string, number>;
  repo_count?: number;
  new_repo_count?: number;
}

/** Error body of the API, e.g. 429 { error, reason: 'weekly', retry_at }. */
export interface ApiError {
  error: string;
  reason?: TrialReason;
  retry_at?: number;
}

export function isActive(job: Job | null): boolean {
  return job?.status === 'queued' || job?.status === 'running';
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

  startJob(): Observable<Job> {
    return this.http.post<Job>('/api/jobs', null);
  }

  job(jobId: string): Observable<Job> {
    return this.http.get<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
  }

  /** 404 when the user has never started a run. */
  latestJob(): Observable<Job> {
    return this.http.get<Job>('/api/jobs/latest');
  }
}
