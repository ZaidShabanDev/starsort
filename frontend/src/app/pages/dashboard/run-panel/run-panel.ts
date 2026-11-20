import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DOCUMENT, DestroyRef, OnInit, computed, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, Subscription, catchError, switchMap, takeWhile, timer } from 'rxjs';
import { ApiError, Job, StarsortApi, TrialStatus, isActive } from '../../../starsort-api';

export const POLL_MS = 3000;
export const DOWNLOAD_NAME = 'awesome-stars.md';
const START_FAILED = 'Could not start the run. Please try again.';

@Component({
  selector: 'app-run-panel',
  imports: [DatePipe],
  templateUrl: './run-panel.html',
  styleUrl: './run-panel.scss',
})
export class RunPanel implements OnInit {
  private readonly api = inject(StarsortApi);
  private readonly destroyRef = inject(DestroyRef);
  private readonly document = inject(DOCUMENT);

  readonly trial = input.required<TrialStatus>();
  /** A run started, was refused or ended, so the dashboard's trial status is out of date. */
  readonly trialChanged = output<void>();

  protected readonly job = signal<Job | null>(null);
  protected readonly starting = signal(false);
  protected readonly startError = signal<string | null>(null);
  protected readonly copied = signal(false);

  protected readonly active = computed(() => isActive(this.job()));
  protected readonly percent = computed(() => {
    const job = this.job();
    return job && job.batches_total > 0 ? Math.round((job.batches_done / job.batches_total) * 100) : 0;
  });
  protected readonly categories = computed(() =>
    Object.entries(this.job()?.categories ?? {})
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)),
  );

  private polling?: Subscription;

  ngOnInit(): void {
    this.api.latestJob().subscribe({
      next: (job) => this.show(job),
      error: () => this.job.set(null), // 404: no runs yet
    });
  }

  protected start(): void {
    this.starting.set(true);
    this.startError.set(null);
    this.api.startJob().subscribe({
      next: (job) => {
        this.starting.set(false);
        this.show(job);
        this.trialChanged.emit();
      },
      error: (error: HttpErrorResponse) => {
        const body = error.error as Partial<ApiError> | null;
        this.starting.set(false);
        this.startError.set(typeof body?.error === 'string' ? body.error : START_FAILED);
        this.trialChanged.emit();
      },
    });
  }

  protected async copy(): Promise<void> {
    const markdown = this.job()?.markdown;
    if (!markdown) {
      return;
    }
    try {
      await navigator.clipboard.writeText(markdown);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2000);
    } catch {
      this.copied.set(false); // clipboard blocked by the browser: the text is still on the page to select
    }
  }

  protected download(): void {
    const markdown = this.job()?.markdown;
    if (!markdown) {
      return;
    }
    const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown' }));
    const link = this.document.createElement('a');
    link.href = url;
    link.download = DOWNLOAD_NAME;
    link.click();
    URL.revokeObjectURL(url);
  }

  private show(job: Job): void {
    this.job.set(job);
    if (isActive(job)) {
      this.watch(job.job_id);
    }
  }

  private watch(jobId: string): void {
    this.polling?.unsubscribe();
    this.polling = timer(POLL_MS, POLL_MS)
      .pipe(
        switchMap(() => this.api.job(jobId).pipe(catchError(() => EMPTY))), // a failed check just waits for the next one
        takeWhile((job) => isActive(job), true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        this.job.set(job);
        if (!isActive(job)) {
          this.trialChanged.emit();
        }
      });
  }
}
