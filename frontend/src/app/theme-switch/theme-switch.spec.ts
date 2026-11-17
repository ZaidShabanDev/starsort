import { TestBed } from '@angular/core/testing';
import { ThemeSwitch } from './theme-switch';

describe('ThemeSwitch', () => {
  beforeEach(() => {
    localStorage.clear();
    TestBed.configureTestingModule({ imports: [ThemeSwitch] });
  });

  afterEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset['theme'];
  });

  function pressed(page: HTMLElement): string | undefined {
    return page.querySelector('[aria-pressed="true"]')?.textContent?.trim();
  }

  it('starts on System and switches to Dark when clicked', async () => {
    const fixture = TestBed.createComponent(ThemeSwitch);
    await fixture.whenStable();
    const page = fixture.nativeElement as HTMLElement;

    expect(pressed(page)).toContain('System');

    const dark = Array.from(page.querySelectorAll('button')).find((button) => button.textContent?.includes('Dark'));
    dark?.click();
    await fixture.whenStable();

    expect(pressed(page)).toContain('Dark');
    expect(document.documentElement.dataset['theme']).toBe('dark');
  });
});
