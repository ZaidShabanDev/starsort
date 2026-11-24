import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ThemeSwitch } from '../../theme-switch/theme-switch';

@Component({
  selector: 'app-privacy',
  imports: [RouterLink, ThemeSwitch],
  templateUrl: './privacy.html',
  styleUrl: './privacy.scss',
})
export class Privacy {
  protected readonly githubAppsUrl = 'https://github.com/settings/applications';
}
