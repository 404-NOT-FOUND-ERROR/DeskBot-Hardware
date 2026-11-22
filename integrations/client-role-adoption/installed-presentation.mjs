import { createHash } from 'node:crypto';
import { InputError } from './input-store.mjs';
import { canonicalCharacterId } from './world-definition.mjs';

const PRESENTATION_SCHEMA = 'deskbot.installed-presentation.v1';
const MAX_PERSONA_LENGTH = 2000;
const MAX_STYLE_LENGTH = 500;

function text(value, limit, { allowEmpty = false, multiline = false } = {}) {
  if (typeof value !== 'string' || [...value].length > limit || (!allowEmpty && value.trim() === '')) return false;
  return !(multiline ? /[\x00-\x08\x0b\x0c\x0e-\x1f]/ : /[\x00-\x1f]/).test(value);
}

function voiceMapping(catalog, provider, voiceId) {
  const key = `${provider}:${voiceId}`;
  const value = catalog instanceof Map ? catalog.get(key) : catalog?.[key];
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (!text(value.target, 64) || !text(value.voice_id, 96)) return null;
  return {
    target: value.target,
    voice_id: value.voice_id,
    ...(text(value.profile_id, 96) ? { profile_id: value.profile_id } : {}),
    ...(text(value.voice_name, 96) ? { voice_name: value.voice_name } : {}),
    supports_speaking_style: value.supports_speaking_style === true,
  };
}

function freeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const item of Object.values(value)) freeze(item);
    Object.freeze(value);
  }
  return value;
}

export function createInstalledPresentationResolver({ getInstallation, voiceCatalog = {} } = {}) {
  if (typeof getInstallation !== 'function') throw new TypeError('getInstallation is required');

  return function resolveInstalledPresentation({ character_id, device_id = null, installation_id } = {}) {
    if (!text(character_id, 128) || (device_id !== null && !text(device_id, 128))
        || (installation_id != null && !text(installation_id, 128))) {
      throw new InputError(400, 'installed_presentation_identity_required', '安装配置需要有效的个体与设备编号。');
    }
    // Explicit null freezes the absence of an installation at listen.start.
    if (installation_id === null) return null;
    const actorId = canonicalCharacterId(character_id);
    let stored;
    try { stored = getInstallation({ character_id: actorId, device_id, installation_id: installation_id ?? null }); }
    catch (error) {
      if (error.code?.startsWith('installation_') && Number.isInteger(error.status))
        throw new InputError(error.status, error.code, error.message);
      throw error;
    }
    if (!stored) {
      if (installation_id) throw new InputError(409, 'installed_presentation_missing', '找不到录音开始时的安装档案。');
      return null;
    }
    const row = stored.installation ?? stored.current?.installation ?? null;
    if (!row || row.character_id !== actorId || (device_id && row.device_id !== device_id)
        || (installation_id && row.installation_id !== installation_id)) {
      throw new InputError(409, 'installed_presentation_identity_conflict', '安装档案与当前个体或设备不一致。');
    }
    const profile = row.profile;
    if (!profile || !text(profile.role_id, 64) || !text(profile.expression_role_id, 64)
        || !text(profile.display_name, 80) || !text(profile.persona, MAX_PERSONA_LENGTH, { allowEmpty: true, multiline: true })
        || !Number.isSafeInteger(profile.profile_revision) || profile.profile_revision < 1
        || !profile.voice || typeof profile.voice !== 'object' || Array.isArray(profile.voice)
        || !['unconfigured', 'xiaozhi', 'gateway'].includes(profile.voice.provider)
        || !text(profile.voice.voice_id, 96, { allowEmpty: true })
        || !text(profile.voice.speaking_style, MAX_STYLE_LENGTH, { allowEmpty: true, multiline: true })) {
      throw new InputError(422, 'installed_presentation_profile_invalid', '安装档案的形态配置不完整。');
    }

    const requestedVoice = {
      provider: profile.voice.provider,
      voice_id: profile.voice.voice_id,
      speaking_style: profile.voice.speaking_style,
    };
    let voice;
    if (requestedVoice.provider === 'unconfigured' || requestedVoice.voice_id.trim() === '') {
      voice = { status: 'preserve_existing', requested: requestedVoice, applied: null, reason: null };
    } else {
      const mapped = voiceMapping(voiceCatalog, requestedVoice.provider, requestedVoice.voice_id);
      voice = mapped
        ? { status: 'supported', requested: requestedVoice, applied: mapped,
          style_status: requestedVoice.speaking_style
            ? (mapped.supports_speaking_style ? 'supported' : 'unsupported')
            : 'not_configured',
          reason: requestedVoice.speaking_style && !mapped.supports_speaking_style ? 'speaking_style_not_supported' : null }
        : { status: 'unsupported', requested: requestedVoice, applied: null,
          style_status: requestedVoice.speaking_style ? 'unsupported' : 'not_configured',
          reason: `voice_not_mapped:${requestedVoice.provider}:${requestedVoice.voice_id}` };
    }

    const profileSha = row.binding?.profile_sha256 ?? stored.profile_sha256 ?? null;
    const snapshotCore = {
      schema: PRESENTATION_SCHEMA,
      installation_id: row.installation_id,
      installation_revision: stored.source_revision ?? null,
      character_id: actorId,
      device_id: row.device_id,
      shell_id: row.shell_id,
      role_id: profile.role_id,
      expression_role_id: profile.expression_role_id,
      profile_revision: profile.profile_revision,
      profile_sha256: profileSha,
      display_name: profile.display_name,
      persona: profile.persona,
      voice,
    };
    const snapshot_id = createHash('sha256').update(JSON.stringify(snapshotCore)).digest('hex');
    return freeze({ ...snapshotCore, snapshot_id });
  };
}

export { PRESENTATION_SCHEMA };

// Mapping support describes the request. Only the synthesis response can
// attest the provider and voice that produced audio.
export function describeVoiceSynthesis(selection, { completed = false, requestedProfile = null,
  reportedProfile = null, provider = null, actualVoice = null, error = null } = {}) {
  const report = { status: 'not_requested', requested_profile: requestedProfile,
    reported_profile: reportedProfile, provider, actual_voice: actualVoice, reason: null };
  if (error) return { ...report, status: 'failed', reason: error.code ?? 'tts_failed' };
  if (!completed) return report;
  if (selection?.status !== 'supported') return { ...report, status: 'existing', reason: selection?.reason ?? null };
  const selected = selection.applied;
  if (!provider) return { ...report, status: 'unverified', reason: 'voice_provider_unreported' };
  if (provider !== selected.target) return { ...report, status: 'mismatch', reason: 'voice_target_mismatch' };
  const reportedVoice = actualVoice ?? reportedProfile?.voice_id ?? null;
  const expectedVoice = actualVoice ? selected.voice_name ?? selected.voice_id : selected.voice_id;
  if (!reportedVoice) return { ...report, status: 'unverified', reason: 'voice_identity_unreported' };
  return reportedVoice === expectedVoice
    ? { ...report, status: 'applied' }
    : { ...report, status: 'mismatch', reason: 'voice_identity_mismatch' };
}
