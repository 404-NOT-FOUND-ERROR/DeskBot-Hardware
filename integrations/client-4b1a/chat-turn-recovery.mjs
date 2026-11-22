// Checkpoints share the client's SQLite store. Prepared replies are replayed
// through the original idempotent outbox, never through another model call.
export function createTurnRecovery(persistence) {
  const records = new Map((persistence?.list('chat.recovery') ?? []).map(row => [row.turn_id, row]));
  function save(row) {
    persistence?.put('chat.recovery', row.turn_id, row);
    records.set(row.turn_id, structuredClone(row));
  }
  return {
    get: id => records.get(id) ?? null,
    correlated: event => [...records.values()].find(row => row.input.character_id === event.character_id
      && event.correlation_id && row.input.correlation_id === event.correlation_id) ?? null,
    start(input) { save({ turn_id: input.event_id, phase: 'started', input }); },
    prepare(turn) { save({ turn_id: turn.turn_id, phase: 'prepared', input: turn.input_event, turn }); },
    commit(turn, correlation) {
      const write = () => {
        persistence?.put('chat.turns', turn.turn_id, turn);
        if (correlation) persistence?.put('chat.correlations', correlation.correlation_key, correlation);
        persistence?.put('chat.recovery', turn.turn_id, { turn_id: turn.turn_id, phase: 'completed', input: turn.input_event });
      };
      if (persistence?.transaction) persistence.transaction(write);
      else write();
      records.set(turn.turn_id, { turn_id: turn.turn_id, phase: 'completed', input: turn.input_event });
    },
  };
}
