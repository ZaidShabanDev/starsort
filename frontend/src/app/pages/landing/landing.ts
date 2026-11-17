import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LOGIN_URL, StarsortApi } from '../../starsort-api';
import { ThemeSwitch } from '../../theme-switch/theme-switch';

@Component({
  selector: 'app-landing',
  imports: [RouterLink, ThemeSwitch],
  templateUrl: './landing.html',
  styleUrl: './landing.scss',
})
export class Landing implements OnInit {
  private readonly api = inject(StarsortApi);

  protected readonly loginUrl = LOGIN_URL;
  protected readonly signedIn = signal(false);

  ngOnInit(): void {
    this.api.me().subscribe({
      next: () => this.signedIn.set(true),
      error: () => this.signedIn.set(false),
    });
  }
}
