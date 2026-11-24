import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { ApiError, Me, StarsortApi, TrialStatus } from '../../starsort-api';
import { ThemeSwitch } from '../../theme-switch/theme-switch';
import { RunPanel } from './run-panel/run-panel';

export type BannerTone = 'ok' | 'wait' | 'stop';

@Component({
  selector: 'app-dashboard',
  imports: [DatePipe, RouterLink, ThemeSwitch, RunPanel],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard implements OnInit {
  private readonly api = inject(StarsortApi);
  private readonly router = inject(Router);

  protected readonly me = signal<Me | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly confirmingDelete = signal(false);
  protected readonly deleting = signal(false);
  protected readonly deleteError = signal<string | null>(null);

  ngOnInit(): void {
    this.loadMe();
  }

  /** Also called when a run starts or ends, so the trial banner shows the new status. */
  protected loadMe(): void {
    this.api.me().subscribe({
      next: (me) => this.me.set(me),
      error: (error: HttpErrorResponse) => {
        if (error.status === 401) {
          void this.router.navigateByUrl('/');
          return;
        }
        this.error.set('Could not load your account. Please refresh the page.');
      },
    });
  }

  protected tone(trial: TrialStatus): BannerTone {
    if (trial.can_run) {
      return 'ok';
    }
    return trial.reason === 'budget' ? 'stop' : 'wait';
  }

  protected deleteMyData(): void {
    this.deleting.set(true);
    this.deleteError.set(null);
    this.api.deleteMyData().subscribe({
      next: () => void this.router.navigateByUrl('/?deleted=1'),
      error: (error: HttpErrorResponse) => {
        this.deleting.set(false);
        this.confirmingDelete.set(false);
        this.deleteError.set((error.error as ApiError | null)?.error ?? 'Could not delete your data. Please try again.');
      },
    });
  }

  protected signOut(): void {
    this.api.logout().subscribe({
      next: () => void this.router.navigateByUrl('/'),
      error: () => void this.router.navigateByUrl('/'),
    });
  }
}
