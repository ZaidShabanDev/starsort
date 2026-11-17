import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Me, StarsortApi, TrialStatus } from '../../starsort-api';

export type BannerTone = 'ok' | 'wait' | 'stop';

@Component({
  selector: 'app-dashboard',
  imports: [DatePipe, RouterLink],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard implements OnInit {
  private readonly api = inject(StarsortApi);
  private readonly router = inject(Router);

  protected readonly me = signal<Me | null>(null);
  protected readonly error = signal<string | null>(null);

  ngOnInit(): void {
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

  protected signOut(): void {
    this.api.logout().subscribe({
      next: () => void this.router.navigateByUrl('/'),
      error: () => void this.router.navigateByUrl('/'),
    });
  }
}
