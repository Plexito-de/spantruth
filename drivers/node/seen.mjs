/**
 * What the application observed. Compared between the baseline and instrumented runs.
 */
export class Seen {
  constructor() {
    this.text = "";
    this.events = 0;
    this.response_id = null;
    this.usage = null;
  }

  asDict() {
    return {
      text: this.text,
      events: this.events,
      response_id: this.response_id,
      usage: this.usage,
    };
  }

  toJSON() {
    return this.asDict();
  }
}

