import { TestBed } from '@angular/core/testing';
import { THEME_STORAGE_KEY, Theme } from './theme';

describe('Theme', () => {
  const root = document.documentElement;

  beforeEach(() => {
    localStorage.clear();
    delete root.dataset['theme'];
    TestBed.configureTestingModule({});
  });

  afterEach(() => {
    localStorage.clear();
    delete root.dataset['theme'];
  });

  it('follows the system by default', () => {
    const theme = TestBed.inject(Theme);
    TestBed.tick();

    expect(theme.choice()).toBe('system');
    expect(root.dataset['theme']).toBeUndefined();
  });

  it('applies and remembers a chosen theme', () => {
    const theme = TestBed.inject(Theme);

    theme.choice.set('dark');
    TestBed.tick();

    expect(root.dataset['theme']).toBe('dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('starts from the saved choice', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'light');

    expect(TestBed.inject(Theme).choice()).toBe('light');
  });

  it('ignores a broken saved value', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'purple');

    expect(TestBed.inject(Theme).choice()).toBe('system');
  });

  it('going back to system forgets the saved choice', () => {
    const theme = TestBed.inject(Theme);
    theme.choice.set('dark');
    TestBed.tick();

    theme.choice.set('system');
    TestBed.tick();

    expect(root.dataset['theme']).toBeUndefined();
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });
});
