import {requireThat,VoiceProtocolError} from './xiaozhi-codec.mjs';

const modes = {
  protocol_fixture: 'xiaozhi-protocol-fixture',
  xiaozhi_loopback: 'xiaozhi-voice-pilot',
  xiaozhi_device_trial: 'xiaozhi-voice-device-trial',
};

// Both callers use isolated stores. The legacy device commands close without
// a hardware receipt; the WebSocket session owns transport of the reply.
export class OriginalClientVoiceBridge {
  constructor(baseUrl,{fetchImpl = fetch, timeoutMs = 60000, mode = 'protocol_fixture'} = {}) {
    const url = new URL(baseUrl);
    requireThat(url.protocol === 'http:' && url.hostname === '127.0.0.1'
      && !url.username && !url.password && url.pathname === '/' && !url.search && !url.hash,
      'local_fixture_url_required');
    this.baseUrl = url.origin;
    requireThat(Number.isInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= 60000,'invalid_timeout');
    requireThat(Object.hasOwn(modes,mode),'invalid_voice_bridge_mode');
    this.mode = mode; this.origin = modes[mode];
    this.fetch = fetchImpl; this.timeoutMs = timeoutMs;
    this.tail = Promise.resolve();
    this.unknown = null;
    this.cleanup = new Map();
  }
  async request(path, body, {delivery = false} = {}) {
    try {
      const response = await this.fetch(this.baseUrl + path, {method:'POST',
        headers:{'content-type':'application/json'}, body:JSON.stringify(body), signal:AbortSignal.timeout(this.timeoutMs)});
      const result = await response.json();
      // A 5xx can occur after storage committed. Resolve it using the same
      // immutable ASR event, rather than starting another model call.
      if (delivery && response.status >= 500) throw new VoiceProtocolError('client_delivery_unknown');
      requireThat(response.ok, result.error?.code ?? result.error ?? 'client_http_error');
      return result;
    } catch(error) {
      if (error instanceof VoiceProtocolError) throw error;
      throw new VoiceProtocolError(delivery ? 'client_delivery_unknown' : 'client_http_unavailable');
    }
  }
  async submit(record) {
    try { return await this.request('/api/voice/asr',record.body,{delivery:true}); }
    catch(error) { if (error.code === 'client_delivery_unknown') this.unknown = record; throw error; }
  }
  ingestFinal({turn, transcript, signal}) {
    requireThat(typeof turn.occurred_at === 'string' && Number.isFinite(Date.parse(turn.occurred_at)),
      'turn_time_required');
    // Capture before joining the serial queue; caller mutations cannot alter
    // the identity or payload of a request already waiting for submission.
    const record = {turn:structuredClone(turn),body:{
      event_id:`asr-${turn.utterance_id}`, request_id:`recognize-${turn.utterance_id}`,
      utterance_id:turn.utterance_id, correlation_id:turn.correlation_id, stage:'final',
      occurred_at:turn.occurred_at,
      ...(Object.hasOwn(turn, 'presentation_installation_id')
        ? {presentation_installation_id:turn.presentation_installation_id} : {}),
      raw_utterance:transcript.raw_utterance, clean_utterance:transcript.clean_utterance,
      provider:transcript.provider, character_id:turn.character_id, device_id:turn.device_id,
      shell_id:turn.shell_id ?? null, role_revision:turn.role_revision,
      ...(this.mode !== 'protocol_fixture' ? {source:this.origin} : {}),
      metadata:{origin:this.origin, session_id:turn.session_id, turn_generation:turn.generation},
    }};
    const operation = this.tail.then(async () => {
      // A queued cancelled turn has not entered the original service yet.
      requireThat(!signal.aborted, 'turn_cancelled');
      requireThat(this.unknown === null,'client_recovery_required');
      requireThat(this.cleanup.size === 0,'client_cleanup_required');
      // Once submitted, receive the original result even after an abort so its
      // exact outbox commands can be closed. Never retry an unknown POST here.
      return this.submit(record);
    });
    this.tail = operation.catch(() => {});
    return operation;
  }
  recoveryState() {
    return this.unknown ? {status:'client_delivery_unknown',turn:structuredClone(this.unknown.turn)}
      : this.cleanup.size ? {status:'client_cleanup_required',turns:[...this.cleanup.keys()]} : {status:'ready'};
  }
  recover() {
    return this.unknown ? this.recoverUnknown() : this.retryCleanup();
  }
  recoverUnknown() {
    const operation = this.tail.then(async () => {
      const record = this.unknown;
      if (!record) return {status:'ready'};
      let result;
      try { result = await this.submit(record); }
      catch (error) {
        // The original service checks active and prepared turns before
        // rejecting a started-only turn. Such a turn has no output to settle.
        if (this.mode !== 'protocol_fixture'
          && (error.code === 'turn_recovery_pending' || error.code?.startsWith('llm_'))) {
          this.unknown = null;
          return {status:'ended_without_reply',code:error.code};
        }
        throw error;
      }
      // Recover history and exact outbox IDs, then end the old output. Recovery
      // never sends the returned sentence, expression or audio to a peer.
      await this.finish(result,record.turn,'delivery_unknown');
      this.unknown = null;
      return {status:'recovered_no_replay',turn_id:result.turn?.turn_id};
    });
    this.tail = operation.catch(() => {});
    return operation;
  }
  retryCleanup() {
    const operation = this.tail.then(async () => {
      for (const record of [...this.cleanup.values()]) await this.finish(record.result,record.turn,record.outcome);
      return {status:'cleanup_recovered_no_replay'};
    });
    this.tail = operation.catch(() => {});
    return operation;
  }
  async finish(result, turn, outcome) {
    const commands = (result?.turn?.output_route?.commands ?? []).filter(command=>command.target === 'vocat');
    for (const command of commands) {
      requireThat(command.device_id === turn.device_id && command.character_id === turn.character_id
        && command.role_revision === turn.role_revision && command.correlation_id === turn.correlation_id
        && command.source_event_id === `reply-voice-chat-${turn.utterance_id}`
        && command.source_event_id === result.turn.reply_event.event_id, 'command_binding_mismatch');
    }
    try { for (const command of commands) {
      await this.request(`/api/outbox/${encodeURIComponent(command.command_id)}/ack`, {
        status:'failed', device_id:turn.device_id, target:'vocat', source:this.origin,
        correlation_id:turn.correlation_id, error:{code:this.mode !== 'protocol_fixture' ? 'pilot_legacy_route_disabled' :
          outcome === 'sent' ? this.mode : outcome},
        result:{completion_scope:this.mode, session_id:turn.session_id,
          ...(this.mode !== 'protocol_fixture' ? {network_outcome:outcome} : {}),
          turn_generation:turn.generation, playback_completion:'unobserved'},
      });
    }} catch(error) {
      this.cleanup.set(turn.utterance_id,structuredClone({result,turn,outcome}));
      throw error;
    }
    this.cleanup.delete(turn.utterance_id);
  }
}

export {OriginalClientVoiceBridge as OriginalClientFixture};
