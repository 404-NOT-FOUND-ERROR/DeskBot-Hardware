(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.GrokRoleScheduler = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  class Scheduler {
    constructor(expressions, initial = 'idle') {
      if (!expressions || typeof expressions !== 'object' || typeof initial !== 'string' ||
          !Object.hasOwn(expressions, initial))
        throw new TypeError('a valid initial expression is required');
      this.clips = Object.create(null);
      for (const [state, item] of Object.entries(expressions)) {
        this.clips[state] = {};
        for (const phase of ['entry', 'loop', 'exit']) {
          const count = item?.[phase]?.frames;
          if (!Number.isInteger(count) || count < 1 || count > 192)
            throw new TypeError('bounded entry/loop/exit frame counts required');
          this.clips[state][phase] = count;
        }
      }
      this.current = this.target = initial;
      this.phase = 'entry';
      this.frame = 0;
      this.paused = false;
      this.transitions = 0;
    }
    request(state) {
      if (typeof state !== 'string' || !Object.hasOwn(this.clips, state))
        throw new RangeError('unknown expression');
      // A single pending target: bursts of clicks never queue obsolete clips.
      this.target = state;
      return this.snapshot();
    }
    setPaused(value) {
      if (typeof value !== 'boolean') throw new TypeError('boolean pause value required');
      this.paused = value;
      return this.snapshot();
    }
    advance() {
      if (this.paused) return this.snapshot();
      if (++this.frame < this.clips[this.current][this.phase]) return this.snapshot();
      this.frame = 0;
      if (this.phase === 'entry') this.phase = this.target === this.current ? 'loop' : 'exit';
      else if (this.phase === 'loop') this.phase = this.target === this.current ? 'loop' : 'exit';
      else {
        // Once exit starts, finish at H even if the user picks the old state.
        this.current = this.target;
        this.phase = 'entry';
      }
      ++this.transitions;
      return this.snapshot();
    }
    snapshot() {
      return Object.freeze({current: this.current, target: this.target, phase: this.phase,
        frame: this.frame, frames: this.clips[this.current][this.phase],
        paused: this.paused, transitions: this.transitions});
    }
  }
  return Scheduler;
});
