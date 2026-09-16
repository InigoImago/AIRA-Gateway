import { PrivacyNotice } from '../api/models';

/** A notice as the server renders it, small enough to read in a test. */
export function privacyNotice(overrides: Partial<PrivacyNotice> = {}): PrivacyNotice {
  const language = overrides.language ?? 'de';
  const german = language === 'de';
  return {
    language,
    title: german ? 'Datenschutzhinweise' : 'Privacy notice',
    labels: {
      edition: german ? 'Stand' : 'Last updated',
      language: german ? 'Sprache' : 'Language',
      acknowledge: german ? 'Zur Kenntnis genommen' : 'I have read this notice',
      acknowledged: german ? 'Zuletzt zur Kenntnis genommen am' : 'Last acknowledged on',
      load_failed: german ? 'Nicht geladen.' : 'Not loaded.',
      acknowledge_failed: german ? 'Nicht gespeichert.' : 'Not saved.',
      due_first: german ? 'Bitte zuerst lesen.' : 'Please read this first.',
      due_changed: german ? 'Geändert.' : 'Changed.',
      due_month: german ? 'Neuer Monat.' : 'New month.',
      due_always: german ? 'Jedes Mal.' : 'Every time.',
    },
    sections: [
      {
        key: 'controller',
        title: german ? 'Verantwortlicher' : 'Controller',
        paragraphs: ['Example GmbH'],
        items: [],
      },
      {
        key: 'activities',
        title: german ? 'Verarbeitungen' : 'Processing',
        paragraphs: ['…'],
        items: [],
        activities: [
          {
            key: 'request_record',
            title: german ? 'Protokoll jeder Anfrage' : 'Record of every request',
            fields: [
              { key: 'data', label: german ? 'Welche Daten' : 'Which data', text: 'IP' },
              { key: 'retention', label: german ? 'Speicherdauer' : 'Kept for', text: '400' },
            ],
          },
        ],
      },
      {
        key: 'access',
        title: german ? 'Zugriff' : 'Access',
        paragraphs: ['…'],
        items: ['IT Security: <b>not markup</b>'],
      },
    ],
    version: '2026-09-16.abc',
    edition: '2026-09-16',
    languages: [
      { code: 'de', name: 'Deutsch' },
      { code: 'en', name: 'English' },
    ],
    mode: 'monthly',
    due: 'first',
    acknowledged_at: null,
    ...overrides,
  };
}
