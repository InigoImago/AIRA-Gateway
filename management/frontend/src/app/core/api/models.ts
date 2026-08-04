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
