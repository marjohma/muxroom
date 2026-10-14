export const BASE_DELAY_MS = 500;
export const MAX_DELAY_MS = 10_000;

export function backoffDelay(attempt: number): number {
  return Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS);
}

/** Schedules reconnect attempts with exponential backoff; reset on success. */
export class Reconnector {
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly connect: () => void;

  constructor(connect: () => void) {
    this.connect = connect;
  }

  get attempts(): number {
    return this.attempt;
  }

  schedule(): number {
    this.cancel();
    const delay = backoffDelay(this.attempt);
    this.attempt += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.connect();
    }, delay);
    return delay;
  }

  succeeded(): void {
    this.attempt = 0;
  }

  cancel(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}
