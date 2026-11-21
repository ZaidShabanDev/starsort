import { Location } from '@angular/common';
import { Component, DestroyRef, OnInit, computed, inject, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { pollJob } from '../../../job-polling';
import { Job, StarsortApi, isActive, listsConnectUrl } from '../../../starsort-api';

/** What the API's /dashboard?lists=<status> redirect means. "started" needs no message: the progress shows instead. */
export const LISTS_MESSAGES: Record<string, string> = {
  denied: "GitHub didn't give Starsort permission to edit your lists, so nothing changed.",
  wrong_account: 'You approved with a different GitHub account than the one you are signed in with. Nothing changed.',
  busy: 'A run or sync is already going. Try again when it finishes.',
  no_run: 'Start a run first, so there are categories to sync.',
  expired: 'The approval took too long. Please click Sync again.',
  signed_out: 'You were signed out. Please sign in and click Sync again.',
  error: 'Could not start the sync. Please try again.',
};

@Component({
  selector: 'app-lists-panel',
  templateUrl: './lists-panel.html',
  styleUrl: './lists-panel.scss',
})
export class ListsPanel implements OnInit {
  private readonly api = inject(StarsortApi);
  private readonly destroyRef = inject(DestroyRef);
  private readonly location = inject(Location);

  /** A sync finished, so the dashboard's "a job is running" status is out of date. */
  readonly syncEnded = output<void>();

  protected readonly makePrivate = signal(false);
  protected readonly connectUrl = computed(() => listsConnectUrl(this.makePrivate()));
  protected readonly sync = signal<Job | null>(null);
  protected readonly notice = signal<string | null>(null);

  protected readonly active = computed(() => isActive(this.sync()));
  protected readonly complete = computed(() => {
    const job = this.sync();
    return job?.complete ?? job?.repos_updated === job?.repos_to_update;
  });
  /** Repos skipped because their org blocks Starsort don't count: they come back on every sync. */
  protected readonly changedAnything = computed(() => {
    const job = this.sync();
    return Boolean(job?.lists_created || job?.repos_updated || job?.lists_deleted);
  });
  protected readonly percent = computed(() => {
    const job = this.sync();
    return job && job.batches_total > 0 ? Math.round((job.batches_done / job.batches_total) * 100) : 0;
  });

  ngOnInit(): void {
    this.readRedirectStatus();
    this.api.latestJob('lists').subscribe({
      next: (job) => this.show(job),
      error: () => this.sync.set(null), // 404: never synced
    });
  }

  protected setPrivate(event: Event): void {
    this.makePrivate.set((event.target as HTMLInputElement).checked);
  }

  private readRedirectStatus(): void {
    const [path, query = ''] = this.location.path().split('?');
    const status = new URLSearchParams(query).get('lists');
    if (!status) {
      return;
    }
    if (status !== 'started') {
      this.notice.set(LISTS_MESSAGES[status] ?? LISTS_MESSAGES['error']);
    }
    this.location.replaceState(path); // so a refresh doesn't show the message again
  }

  private show(job: Job): void {
    this.sync.set(job);
    if (isActive(job)) {
      pollJob(this.api, job.job_id)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe((latest) => {
          this.sync.set(latest);
          if (!isActive(latest)) {
            this.syncEnded.emit();
          }
        });
    }
  }
}
