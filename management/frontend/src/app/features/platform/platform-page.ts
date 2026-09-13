import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

/**
 * Platform administration (`FRD-622` FR-5): a menu of platform-wide pages on the left, the chosen
 * page on the right. Reached from the header by the three platform roles; each page's data is
 * authorised by the server, so the menu only decides what is offered.
 */
@Component({
  selector: 'app-platform-page',
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './platform-page.html',
  styleUrl: './platform-page.scss',
})
export class PlatformPage {}
