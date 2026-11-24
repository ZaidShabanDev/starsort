import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

export const REPO_URL = 'https://github.com/ZaidShabanDev/starsort';

@Component({
  selector: 'app-site-footer',
  imports: [RouterLink],
  templateUrl: './site-footer.html',
  styleUrl: './site-footer.scss',
})
export class SiteFooter {
  protected readonly repoUrl = REPO_URL;
  protected readonly licenseUrl = `${REPO_URL}/blob/main/LICENSE`;
  protected readonly githubUrl = 'https://github.com/ZaidShabanDev';
  protected readonly linkedinUrl = 'https://www.linkedin.com/in/zaid-shaban/';
  protected readonly year = new Date().getFullYear();
}
