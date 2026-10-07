export interface MutationTicket {
  signal: AbortSignal;
  current: () => boolean;
  finish: () => void;
}

export class MutationGuard {
  private key = '';
  private generation = 0;
  private controller: AbortController | null = null;

  reset(key: string) {
    this.controller?.abort();
    this.controller = null;
    this.key = key;
    this.generation += 1;
  }

  begin(key: string): MutationTicket | null {
    if (key !== this.key || this.controller) return null;
    const generation = this.generation;
    const controller = new AbortController();
    this.controller = controller;
    const current = () => this.controller === controller && this.key === key
      && this.generation === generation && !controller.signal.aborted;
    return {
      signal: controller.signal,
      current,
      finish: () => { if (this.controller === controller) this.controller = null; },
    };
  }
}
