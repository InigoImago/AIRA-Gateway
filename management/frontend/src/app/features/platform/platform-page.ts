import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { errorMessage } from '../../core/api/error-message';
import { MeService } from '../../core/api/me.service';
import { Me } from '../../core/api/models';
import { Permission, can } from '../../core/auth/roles';

/**
 * Platform administration (`FRD-622` FR-5): a menu of platform-wide pages on the left, the chosen
 * page on the right. Each entry is offered by the permission its page needs (`FRD-614`), so a
 * caller never picks a page that answers 403; the server still authorises every page's data.
 */
@Component({
  selector: 'app-platform-page',
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './platform-page.html',
  styleUrl: './platform-page.scss',
})
export class PlatformPage implements OnInit {
  private readonly meService = inject(MeService);

  protected readonly me = signal<Me | null>(null);
  /** Why the menu is empty, when `/me` could not be read: an empty menu otherwise reads as a boundary. */
  protected readonly menuError = signal<string | null>(null);

  ngOnInit(): void {
    this.meService.get().subscribe({
      next: (me) => this.me.set(me),
      error: (response: unknown) =>
        this.menuError.set(errorMessage(response, 'Could not load which pages you may open.')),
    });
  }

  protected may(permission: Permission): boolean {
    return can(this.me(), permission);
  }
}
