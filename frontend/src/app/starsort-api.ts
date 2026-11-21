import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

// Same origin as the site: CloudFront sends /api/* to API Gateway, so the session cookie goes along.
export const LOGIN_URL = '/api/auth/login';

/** Full-page link: the API sends the browser to GitHub to approve editing star lists, then back to /dashboard?lists=<status>. */
export function listsConnectUrl(makePrivate: boolean): string {
  return makePrivate ? '/api/auth/lists?private=1' : '/api/auth/lists';
}

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
export type JobKind = 'run' | 'lists';

export interface Job {
  job_id: string;
  kind?: JobKind; // missing on jobs from before Lists sync existed: those are runs
  login: string;
  status: JobStatus;
  created_at: number; // Unix seconds
  updated_at: number;
  batches_done: number; // a Lists sync counts repos here
  batches_total: number;
  error?: string;
  // A finished run:
  markdown?: string;
  categories?: Record<string, number>;
  repo_count?: number;
  new_repo_count?: number;
  // A finished Lists sync (complete is false when GitHub's rate limit stopped it early):
  lists_created?: number;
  lists_deleted?: number; // starsort's own lists left empty
  repos_updated?: number;
  repos_to_update?: number;
  repos_skipped?: number; // repos of organizations that block apps like Starsort
  skipped_orgs?: string[];
  complete?: boolean;
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

  /** 404 when the user has never started a run (or a Lists sync). */
  latestJob(kind: JobKind = 'run'): Observable<Job> {
    return kind === 'lists' ? this.http.get<Job>('/api/jobs/latest?kind=lists') : this.http.get<Job>('/api/jobs/latest');
  }
}
