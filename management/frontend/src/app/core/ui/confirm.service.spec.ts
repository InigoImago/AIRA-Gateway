import { TestBed } from '@angular/core/testing';
import { ConfirmService } from './confirm.service';

describe('ConfirmService', () => {
  it('passes the question to the browser and returns the answer', () => {
    TestBed.resetTestingModule();
    const asked: string[] = [];
    const original = window.confirm;
    window.confirm = (message?: string) => {
      asked.push(message ?? '');
      return false;
    };
    try {
      const service = TestBed.inject(ConfirmService);
      expect(service.ask('Revoke this key?')).toBe(false);
      expect(asked).toEqual(['Revoke this key?']);
    } finally {
      window.confirm = original;
    }
  });

  it('does not block when the answer is yes', () => {
    TestBed.resetTestingModule();
    const original = window.confirm;
    window.confirm = () => true;
    try {
      expect(TestBed.inject(ConfirmService).ask('Remove?')).toBe(true);
    } finally {
      window.confirm = original;
    }
  });
});
