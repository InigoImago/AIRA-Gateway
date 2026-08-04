export interface Me {
  subject: string;
  username: string;
  email: string;
  roles: string[];
  use_cases: string[];
}

export interface UseCase {
  slug: string;
  name: string;
  description: string;
  processing_notes: string;
  created_at?: string;
  updated_at?: string;
}

export interface Membership {
  username: string;
  role: string;
  created_at?: string;
}

export interface ApiKey {
  prefix: string;
  label: string;
  owner: string;
  is_active: boolean;
  created_at?: string;
  revoked_at?: string | null;
}

/** Issue response — the only time the plaintext key is ever returned. */
export interface IssuedApiKey {
  api_key: string;
  prefix: string;
  label: string;
  use_case: string;
}
