// Latest-request-wins sequencer (docs/SPEC.md, Interaction and states):
// each new request claims the sequence; when a response lands its claim
// decides whether it is still the newest. A response from an older request
// is dropped and must not overwrite data, timestamps or failure state.
export function createSequence(): {
  /** Claim the sequence for a new request; returns `true` while this is the
   *  latest claim. */
  next(): () => boolean;
} {
  let current = 0;
  return {
    next() {
      const id = ++current;
      return () => id === current;
    },
  };
}
