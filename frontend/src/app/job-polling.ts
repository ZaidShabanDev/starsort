import { EMPTY, Observable, catchError, switchMap, takeWhile, timer } from 'rxjs';
import { Job, StarsortApi, isActive } from './starsort-api';

export const POLL_MS = 3000;

/** Checks the job every POLL_MS until it is done or failed. A failed check is skipped; the next one tries again. */
export function pollJob(api: StarsortApi, jobId: string): Observable<Job> {
  return timer(POLL_MS, POLL_MS).pipe(
    switchMap(() => api.job(jobId).pipe(catchError(() => EMPTY))),
    takeWhile((job) => isActive(job), true),
  );
}
