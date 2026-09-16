/** The privacy notice as `GET /api/v1/privacy-notice` renders it (`FRD-625`). */

/** One of the five statements every processing activity makes. */
export interface PrivacyActivityField {
  key: 'data' | 'purpose' | 'legal_basis' | 'retention' | 'recipients';
  label: string;
  text: string;
}

export interface PrivacyActivity {
  key: string;
  title: string;
  fields: PrivacyActivityField[];
}

export interface PrivacySection {
  key: string;
  title: string;
  paragraphs: string[];
  items: string[];
  /** Only on the `activities` section: the register the notice is written from. */
  activities?: PrivacyActivity[];
}

/** Why the window opens — the server decides, the console says so. */
export type PrivacyDue = 'first' | 'changed' | 'month' | 'always';

export interface PrivacyNotice {
  language: string;
  title: string;
  /** The console's words around the notice, in the notice's language. */
  labels: Record<string, string>;
  sections: PrivacySection[];
  /** What an acknowledgement names. Covers every language, so switching language asks nothing. */
  version: string;
  /** The date the content last changed, ISO 8601. */
  edition: string;
  languages: { code: string; name: string }[];
  mode: 'once' | 'monthly' | 'always';
  /** `null` when this reader need not acknowledge now. */
  due: PrivacyDue | null;
  /** When this reader last acknowledged this version, or `null`. */
  acknowledged_at: string | null;
}

export interface PrivacyAcknowledgement {
  version: string;
  language: string;
  first_at: string;
  last_at: string;
}
