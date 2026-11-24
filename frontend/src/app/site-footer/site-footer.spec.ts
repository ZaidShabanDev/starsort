import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { SiteFooter } from './site-footer';

describe('SiteFooter', () => {
  it('links to the repo, the license, the privacy page and the author', async () => {
    TestBed.configureTestingModule({ imports: [SiteFooter], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(SiteFooter);
    await fixture.whenStable();
    const page = fixture.nativeElement as HTMLElement;

    const links = Array.from(page.querySelectorAll('a')).map((link) => link.getAttribute('href'));

    expect(links).toEqual([
      'https://github.com/ZaidShabanDev/starsort',
      'https://github.com/ZaidShabanDev',
      'https://www.linkedin.com/in/zaid-shaban/',
      'https://github.com/ZaidShabanDev/starsort/blob/main/LICENSE',
      '/privacy',
    ]);
    expect(page.textContent).toContain('MIT');
  });
});
