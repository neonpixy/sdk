// Bridge types — the raw window.omninet API injected by the native shell.
// These types are stable and hand-maintained. The generated SDK wraps them.

export interface PipelineStep {
  id: string;
  op: string;
  input: Record<string, unknown>;
}

export interface PipelineRequest {
  source: string;
  steps: PipelineStep[];
}

export interface PipelineResult {
  ok: true;
  result: unknown;
}

export interface PipelineError {
  ok: false;
  error: string;
  failed_step: string;
  step_index: number;
}

export type PipelineResponse = PipelineResult | PipelineError;

export interface OmninetBridge {
  run(pipeline: PipelineRequest): Promise<PipelineResponse>;
  platform(op: string, input: Record<string, unknown>): Promise<{ ok: boolean; result?: unknown; error?: string }>;
  capabilities(): Promise<{ namespace: string; operations: { name: string; description: string; permission: string }[] }[]>;
  on(event: string, handler: (data: unknown) => void): void;
  off(event: string, handler: (data: unknown) => void): void;
}

declare global {
  interface Window {
    omninet: OmninetBridge;
  }
}
