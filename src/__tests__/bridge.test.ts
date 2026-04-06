import { describe, it, expect } from 'vitest';
import type {
  PipelineStep,
  PipelineRequest,
  PipelineResult,
  PipelineError,
  PipelineResponse,
} from '../bridge.js';

describe('PipelineStep', () => {
  it('constructs a valid step', () => {
    const step: PipelineStep = {
      id: 'step-1',
      op: 'crown.whoami',
      input: {},
    };
    expect(step.id).toBe('step-1');
    expect(step.op).toBe('crown.whoami');
    expect(step.input).toEqual({});
  });

  it('accepts arbitrary input keys', () => {
    const step: PipelineStep = {
      id: 'step-2',
      op: 'vault.store',
      input: { key: 'notes', value: 'hello', ttl: 3600 },
    };
    expect(step.input).toHaveProperty('key', 'notes');
    expect(step.input).toHaveProperty('value', 'hello');
    expect(step.input).toHaveProperty('ttl', 3600);
  });
});

describe('PipelineRequest', () => {
  it('constructs a single-step request', () => {
    const req: PipelineRequest = {
      source: 'omnigram:test',
      steps: [{ id: 's1', op: 'crown.whoami', input: {} }],
    };
    expect(req.source).toBe('omnigram:test');
    expect(req.steps).toHaveLength(1);
  });

  it('constructs a multi-step pipeline', () => {
    const req: PipelineRequest = {
      source: 'omnigram:editor',
      steps: [
        { id: 's1', op: 'crown.whoami', input: {} },
        { id: 's2', op: 'vault.get', input: { key: 'draft' } },
        { id: 's3', op: 'hall.write', input: { path: '/doc.idea', data: 'content' } },
      ],
    };
    expect(req.steps).toHaveLength(3);
    expect(req.steps[0].op).toBe('crown.whoami');
    expect(req.steps[2].op).toBe('hall.write');
  });
});

describe('PipelineResponse', () => {
  it('represents a success result', () => {
    const res: PipelineResponse = {
      ok: true,
      result: { identity: 'crown:abc123' },
    };
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.result).toEqual({ identity: 'crown:abc123' });
    }
  });

  it('represents an error result', () => {
    const res: PipelineResponse = {
      ok: false,
      error: 'Permission denied',
      failed_step: 's2',
      step_index: 1,
    };
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.error).toBe('Permission denied');
      expect(res.failed_step).toBe('s2');
      expect(res.step_index).toBe(1);
    }
  });

  it('narrows types via ok discriminant', () => {
    const success: PipelineResponse = { ok: true, result: 42 };
    const failure: PipelineResponse = {
      ok: false,
      error: 'timeout',
      failed_step: 's1',
      step_index: 0,
    };

    // Type narrowing works at runtime
    if (success.ok) {
      expect((success as PipelineResult).result).toBe(42);
    }
    if (!failure.ok) {
      expect((failure as PipelineError).error).toBe('timeout');
    }
  });
});
