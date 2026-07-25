export class DebouncedRefreshQueue {
  private timer: NodeJS.Timeout | undefined;
  private running = false;
  private queued = false;

  constructor(
    private readonly delayMs: number,
    private readonly run: () => Promise<void>,
  ) {}

  request(): void {
    if (this.running) {
      this.queued = true;
      return;
    }
    if (this.timer) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.execute();
    }, this.delayMs);
  }

  dispose(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
  }

  private async execute(): Promise<void> {
    this.running = true;
    try {
      await this.run();
    } finally {
      this.running = false;
      if (this.queued) {
        this.queued = false;
        this.request();
      }
    }
  }
}
