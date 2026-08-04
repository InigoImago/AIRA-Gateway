import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';
import { MeService } from './core/api/me.service';
import { Me } from './core/api/models';
import { AuthService } from './core/auth/auth.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App implements OnInit {
  private readonly meService = inject(MeService);
  private readonly auth = inject(AuthService);

  protected readonly title = signal('AIRA Gateway');
  protected readonly me = signal<Me | null>(null);

  ngOnInit(): void {
    if (this.auth.isAuthenticated()) {
      this.meService.get().subscribe((me) => this.me.set(me));
    }
  }

  protected hasRole(role: string): boolean {
    return this.me()?.roles.includes(role) ?? false;
  }

  protected logout(): void {
    this.auth.logout();
  }
}
