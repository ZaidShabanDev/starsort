import { Routes } from '@angular/router';
import { Dashboard } from './pages/dashboard/dashboard';
import { Landing } from './pages/landing/landing';

export const routes: Routes = [
  { path: '', component: Landing, title: 'Starsort' },
  { path: 'dashboard', component: Dashboard, title: 'Dashboard | Starsort' },
  { path: '**', redirectTo: '' },
];
