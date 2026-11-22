import {randomUUID} from 'node:crypto';
import {UPLINK, DOWNLINK, MAX_PACKETS, MAX_AUDIO_BYTES, MAX_PACKET_BYTES,
  parseHello, checkAudio, decodeAudio, encodeAudio, requireThat} from './xiaozhi-codec.mjs';
import {VoiceProtocolError} from './xiaozhi-codec.mjs';

const SEMANTICS = new Set(['neutral','happy']);

// Completion is bounded even when a provider ignores its AbortSignal. The
// provider still receives cancellation, and late results cannot enter output.
function boundedStage(operation, turnSignal, timeoutMs, code) {
  return new Promise((resolve,reject) => {
    const stage = new AbortController();
    const signal = AbortSignal.any([turnSignal,stage.signal]);
    let settled = false;
    const finish = (callback,value) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal.removeEventListener('abort',abort);
      callback(value);
    };
    const abort = () => finish(reject,signal.reason ?? new VoiceProtocolError('turn_cancelled'));
    const timer = setTimeout(() => stage.abort(new VoiceProtocolError(code)),timeoutMs);
    signal.addEventListener('abort',abort,{once:true});
    if (signal.aborted) { abort(); return; }
    try { Promise.resolve(operation(signal)).then(value=>finish(resolve,value),error=>finish(reject,error)); }
    catch(error) { finish(reject,error); }
  });
}

// One instance belongs to one authenticated connection. The transport owner
// must close an old instance before replacing its connection or role binding.
export class VoiceSession {
  constructor({binding, client, recognize, synthesize, peer, now = () => Date.now(), sessionId = randomUUID(),
    stageTimeoutMs = 15000, resolvePresentationAtStart = null, completionScope = 'protocol_fixture',
    selectExpression = expression => expression}) {
    for (const key of ['character_id','device_id','role_revision']) {
      requireThat(typeof binding?.[key] === 'string' && binding[key].length > 0, 'binding_required');
    }
    this.binding = Object.freeze(structuredClone(binding));
    this.client = client; this.recognize = recognize; this.synthesize = synthesize;
    if (resolvePresentationAtStart !== null && typeof resolvePresentationAtStart !== 'function')
      throw new TypeError('resolvePresentationAtStart must be a function');
    this.resolvePresentationAtStart = resolvePresentationAtStart;
    if (typeof completionScope !== 'string' || completionScope.length === 0) throw new TypeError('completionScope is required');
    this.completionScope = completionScope;
    if (typeof selectExpression !== 'function') throw new TypeError('selectExpression must be a function');
    this.selectExpression = selectExpression;
    this.peer = peer; this.now = now; this.sessionId = sessionId;
    requireThat(Number.isInteger(stageTimeoutMs) && stageTimeoutMs > 0 && stageTimeoutMs <= 60000,'invalid_timeout');
    this.stageTimeoutMs = stageTimeoutMs; this.lastCleanupError = null;
    this.state = 'awaiting_hello'; this.generation = 0; this.active = null;
    this.version = null; this.jobs = new Set();
  }
  json(value) {
    requireThat(this.peer.sendJson({...value, session_id:this.sessionId}) !== false, 'transport_failed');
  }
  emit(turn,value) { requireThat(this.current(turn),'turn_cancelled'); this.json(value); }
  current(turn) { return this.state !== 'closed' && this.active === turn && !turn.controller.signal.aborted; }
  invalidate({notify = true} = {}) {
    const previous = this.active;
    this.active = null; this.generation += 1;
    if (this.state !== 'closed') this.state = this.version === null ? 'awaiting_hello' : 'ready';
    if (previous) {
      previous.controller.abort();
      if (notify && previous.speaking && this.state !== 'closed') {
        previous.speaking = false; this.json({type:'tts', state:'stop'});
      }
    }
  }
  receiveJson(message) {
    requireThat(this.state !== 'closed', 'session_closed');
    requireThat(message && typeof message === 'object' && !Array.isArray(message)
      && Buffer.byteLength(JSON.stringify(message)) <= 8192, 'invalid_control');
    if (this.state === 'awaiting_hello') {
      this.version = parseHello(message);
      this.json({type:'hello', version:this.version, transport:'websocket', audio_params:{...DOWNLINK}});
      this.state = 'ready'; return;
    }
    requireThat(message.session_id === this.sessionId, 'stale_session');
    if (message.type === 'abort') { this.invalidate(); return; }
    requireThat(message.type === 'listen', 'unsupported_control');
    if (message.state === 'start') {
      // Auto/realtime need a VAD/endpointer. Explicitly negotiate that in 4b2b.
      requireThat(message.mode === 'manual', 'manual_listen_required');
      // A submitted original HTTP call remains alive for exact outbox cleanup.
      // Permit one following turn, then report busy until those calls settle.
      requireThat(this.jobs.size < 2,'voice_busy');
      this.invalidate();
      // Freeze the installed form when capture begins, including an explicit
      // null that distinguishes an unconfigured device from a late lookup.
      const presentation = this.resolvePresentationAtStart?.(this.binding) ?? null;
      requireThat(!presentation || (typeof presentation === 'object' && !Array.isArray(presentation)
        && typeof presentation.installation_id === 'string' && presentation.installation_id.length > 0
        && presentation.installation_id.length <= 128), 'invalid_presentation_snapshot');
      this.active = {
        ...this.binding, session_id:this.sessionId, generation:this.generation,
        ...(this.resolvePresentationAtStart ? {
          shell_id:presentation?.shell_id ?? null,
          role_revision:presentation?.role_revision ?? this.binding.role_revision,
        } : {}),
        ...(this.resolvePresentationAtStart || this.binding.presentation_installation_id !== undefined ? {
          presentation_installation_id: this.resolvePresentationAtStart
            ? presentation?.installation_id ?? null : this.binding.presentation_installation_id,
        } : {}),
        utterance_id:`xz-${randomUUID()}`, correlation_id:`xz-${randomUUID()}`,
        controller:new AbortController(), startedAt:this.now(), packets:[], byteCount:0,
        completion:null, speaking:false,
      };
      this.state = 'listening'; return;
    }
    requireThat(message.state === 'stop', 'unsupported_listen_state');
    const turn = this.active;
    requireThat(turn, 'not_listening');
    if (turn.completion) return turn.completion;
    requireThat(this.state === 'listening', 'not_listening');
    this.state = 'processing';
    turn.completion = this.process(turn);
    this.jobs.add(turn.completion);
    turn.completion.finally(() => this.jobs.delete(turn.completion));
    return turn.completion;
  }
  receiveAudio(bytes) {
    requireThat(this.state === 'listening' && this.active, 'not_listening');
    const turn = this.active;
    try {
      const packet = decodeAudio(bytes,this.version);
      requireThat(this.now() - turn.startedAt <= 30000 && turn.packets.length < MAX_PACKETS
        && turn.byteCount + packet.payload.length <= MAX_AUDIO_BYTES, 'utterance_too_large');
      turn.packets.push(packet); turn.byteCount += packet.payload.length;
    } catch (error) { this.invalidate(); throw error; }
  }
  async process(turn) {
    let result = null, report;
    try {
      report = await this.execute(turn,value => { result = value; });
    } catch (error) {
      report = {status:this.current(turn) ? (error.code ?? 'voice_failed') : 'turn_cancelled'};
    }
    // Stop speaking before HTTP cleanup, which may wait for a slow backend.
    if (this.current(turn) && turn.speaking) {
      turn.speaking = false;
      try { this.emit(turn,{type:'tts',state:'stop'}); }
      catch { report = {...report,status:'transport_failed'}; }
    }
    if (this.current(turn)) this.state = 'settling';
    // Original service may finish after cancellation. Settle its exact output
    // IDs without touching a newer turn, and keep cleanup failures observable.
    try { if (result) await this.client.finish(result,this.context(turn),report.status); }
    catch (error) {
      turn.cleanupError = error.code ?? error.message;
      this.lastCleanupError = {turn:this.context(turn),error:turn.cleanupError};
      report = {...report,status:'cleanup_failed'};
    }
    if (this.current(turn)) {
      this.state = 'ready';
    }
    turn.packets = []; // Release captured audio while retaining the small retry context.
    turn.outcome = report.status;
    return report;
  }
  async execute(turn, saveResult) {
      requireThat(this.now() - turn.startedAt <= 30000, 'utterance_too_large');
      if (!turn.packets.length) return {status:'empty_audio'};
      const signal = turn.controller.signal;
      const transcript = await boundedStage(stageSignal => this.recognize({packets:turn.packets, audio:{...UPLINK},
        signal:stageSignal, turn:this.context(turn)}),signal,this.stageTimeoutMs,'recognition_timeout');
      if (!this.current(turn)) return {status:'turn_cancelled'};
      requireThat(typeof transcript?.raw_utterance === 'string' && typeof transcript.clean_utterance === 'string'
        && transcript.raw_utterance.length <= 8000 && transcript.clean_utterance.length <= 8000
        && typeof transcript.provider === 'string' && transcript.provider.length > 0, 'invalid_transcript');
      if (!transcript.clean_utterance.trim()) return {status:'empty_transcript'};
      this.emit(turn,{type:'stt', text:transcript.clean_utterance});
      const result = await this.client.ingestFinal({turn:this.context(turn), transcript, signal});
      saveResult(result);
      if (!this.current(turn)) return {status:'turn_cancelled'};
      const reply = result?.turn;
      requireThat(reply?.input_event?.device_id === turn.device_id
        && reply.input_event.character_id === turn.character_id
        && reply.input_event.role_revision === turn.role_revision
        && reply.input_event.correlation_id === turn.correlation_id
        && reply.input_event.event_id === `voice-chat-${turn.utterance_id}`, 'reply_binding_mismatch');
      requireThat(typeof reply.reply === 'string' && reply.reply.trim() && reply.reply.length <= 8000,
        'invalid_reply');
      const plannedExpression = reply.output_plan?.find(p => p.type === 'render.expression')?.expression;
      const expression = this.selectExpression(plannedExpression);
      requireThat(SEMANTICS.has(expression), 'unsupported_expression');
      const voiceAdoption = reply.presentation_adoption?.voice ?? null;
      const audio = await boundedStage(stageSignal => this.synthesize({text:reply.reply, expression, voice_adoption:voiceAdoption,
        installed_presentation:reply.installed_presentation ?? null,
        signal:stageSignal, turn:this.context(turn)}),signal,this.stageTimeoutMs,'synthesis_timeout');
      if (!this.current(turn)) return {status:'turn_cancelled'};
      checkAudio(audio?.audio,DOWNLINK);
      requireThat(Array.isArray(audio.packets) && audio.packets.length > 0 && audio.packets.length <= MAX_PACKETS,
        'invalid_synthesis');
      let bytes = 0;
      // Validate the whole clip before the first speaking event. Audio remains
      // bounded while a future transport supplies pacing and backpressure.
      for (const packet of audio.packets) {
        requireThat(Buffer.isBuffer(packet) && packet.length > 0 && packet.length <= MAX_PACKET_BYTES,
          'invalid_synthesis');
        bytes += packet.length;
        requireThat(bytes <= MAX_AUDIO_BYTES, 'invalid_synthesis');
      }
      turn.speaking = true; this.state = 'speaking';
      this.emit(turn,{type:'tts', state:'start'});
      this.emit(turn,{type:'llm', emotion:expression});
      this.emit(turn,{type:'tts', state:'sentence_start', text:reply.reply});
      for (const [index,packet] of audio.packets.entries()) {
        if (!this.current(turn)) return {status:'turn_cancelled'};
        const accepted = await boundedStage(stageSignal => this.peer.sendAudio(
          encodeAudio(packet,this.version,index * DOWNLINK.frame_duration),{signal:stageSignal}),
          signal,this.stageTimeoutMs,'audio_send_timeout');
        requireThat(accepted !== false,
          'transport_failed');
      }
      if (!this.current(turn)) return {status:'turn_cancelled'};
      this.emit(turn,{type:'tts', state:'stop'}); turn.speaking = false;
      return {status:'sent', completion_scope:this.completionScope, turn_id:reply.turn_id,
        requested_expression:plannedExpression??null, emitted_expression:expression,
        packets:audio.packets.length, playback_completion:'unobserved', voice_adoption:audio.voice_adoption??voiceAdoption,
        actual_voice:audio.voice ?? null};
  }
  context(turn) {
    return {...this.binding, ...(Object.hasOwn(turn,'shell_id') ? {shell_id:turn.shell_id} : {}),
      role_revision:turn.role_revision,
      session_id:turn.session_id, generation:turn.generation,
      utterance_id:turn.utterance_id, correlation_id:turn.correlation_id,
      occurred_at:new Date(turn.startedAt).toISOString(),
      ...(Object.hasOwn(turn, 'presentation_installation_id')
        ? {presentation_installation_id:turn.presentation_installation_id} : {})};
  }
  snapshot() {
    return {state:this.state, session_id:this.sessionId, generation:this.generation,
      last_outcome:this.active?.outcome ?? null, cleanup_error:this.lastCleanupError?.error ?? null,
      pending_jobs:this.jobs.size};
  }
  close() { this.state = 'closed'; this.invalidate({notify:false}); }
  async drain() { return Promise.all([...this.jobs]); }
}
