import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createInstalledPresentationResolver, describeVoiceSynthesis } from '../src/installed-presentation.mjs';

function record({installationId = 'install-a', shellId = 'shell-a', roleId = 'form-round',
  expressionRoleId = 'grok-round-personal-v1', persona = '喜欢留意水边的细节。',
  voice = { provider: 'unconfigured', voice_id: '', speaking_style: '' }} = {}) {
  return {
    source_revision: 12,
    installation_id: installationId,
    installation_sha256: 'a'.repeat(64),
    installation: {
      installation_id: installationId,
      character_id: 'shaping-001',
      device_id: 'device-head-a',
      shell_id: shellId,
      binding: { profile_sha256: 'b'.repeat(64) },
      profile: {
        role_id: roleId,
        expression_role_id: expressionRoleId,
        profile_revision: 3,
        display_name: roleId === 'form-round' ? '圆团' : '原有角色',
        persona,
        voice,
      },
    },
  };
}

test('installation snapshot keeps continuous identity, physical shell and expression role distinct', () => {
  const archived = record();
  const resolve = createInstalledPresentationResolver({ getInstallation: query => {
    assert.deepEqual(query, { character_id: 'shaping-001', device_id: 'device-head-a', installation_id: 'install-a' });
    return archived;
  } });
  const snapshot = resolve({ character_id: 'shaping-001', device_id: 'device-head-a', installation_id: 'install-a' });

  assert.equal(snapshot.character_id, 'shaping-001');
  assert.equal(snapshot.shell_id, 'shell-a');
  assert.equal(snapshot.role_id, 'form-round');
  assert.equal(snapshot.expression_role_id, 'grok-round-personal-v1');
  assert.equal(snapshot.installation_id, 'install-a');
  assert.match(snapshot.snapshot_id, /^[a-f0-9]{64}$/);
  assert.ok(Object.isFrozen(snapshot));
});

test('empty voice preserves the existing voice and mapped voice returns explicit service parameters', () => {
  const rows = new Map([
    ['install-a', record()],
    ['install-b', record({ installationId: 'install-b', shellId: 'shell-b', roleId: 'form-original',
      expressionRoleId: 'xiaozhi-e145', voice: { provider: 'gateway', voice_id: 'cn-soft-01', speaking_style: '温和清楚' } })],
  ]);
  const resolve = createInstalledPresentationResolver({
    getInstallation: ({ installation_id }) => rows.get(installation_id),
    voiceCatalog: {
      'gateway:cn-soft-01': { target: 'windows-system-speech', voice_id: 'zh-CN-XiaoxiaoNeural',
        voice_name: 'Microsoft Huihui Desktop', profile_id: 'deskbot-soft-v1', supports_speaking_style: true },
    },
  });

  assert.equal(resolve({ character_id: 'shaping-001', device_id: 'device-head-a', installation_id: 'install-a' })
    .voice.status, 'preserve_existing');
  const mapped = resolve({ character_id: 'shaping-001', device_id: 'device-head-a', installation_id: 'install-b' });
  assert.equal(mapped.shell_id, 'shell-b');
  assert.equal(mapped.expression_role_id, 'xiaozhi-e145');
  assert.equal(mapped.voice.status, 'supported');
  assert.equal(mapped.voice.applied.voice_name, 'Microsoft Huihui Desktop');
  assert.equal(mapped.voice.style_status, 'supported');
});

test('unknown voice and installation identity mismatch are reported explicitly', () => {
  const resolve = createInstalledPresentationResolver({ getInstallation: () => record({
    voice: { provider: 'xiaozhi', voice_id: 'cloud-voice-9', speaking_style: '' },
  }) });
  const result = resolve({ character_id: 'shaping-001', device_id: 'device-head-a' });
  assert.equal(result.voice.status, 'unsupported');
  assert.equal(result.voice.reason, 'voice_not_mapped:xiaozhi:cloud-voice-9');
  assert.equal(result.voice.applied, null);

  const wrongIdentity = createInstalledPresentationResolver({ getInstallation: () => record({ installationId: 'other' }) });
  assert.throws(() => wrongIdentity({ character_id: 'shaping-001', device_id: 'device-head-a', installation_id: 'install-a' }),
    { code: 'installed_presentation_identity_conflict' });
});

test('absent installation and a missing frozen archive have distinct outcomes', () => {
  let reads = 0;
  const resolve = createInstalledPresentationResolver({ getInstallation: () => { reads++; return null; } });
  const identity = { character_id: 'shaping-001', device_id: 'device-head-a' };
  assert.equal(resolve({ ...identity, installation_id: null }), null);
  assert.equal(reads, 0);
  assert.equal(resolve(identity), null);
  assert.throws(() => resolve({ ...identity, installation_id: 'lost-archive' }), { code: 'installed_presentation_missing' });
});

test('voice mapping and actual synthesis distinguish target, voice and missing service reports', () => {
  const selection = { status: 'supported', applied: { target: 'windows-system-speech',
    voice_id: 'huihui', voice_name: 'Microsoft Huihui Desktop' } };
  assert.equal(describeVoiceSynthesis(selection).status, 'not_requested');
  const completed = { completed: true, provider: 'windows-system-speech', actualVoice: 'Microsoft Huihui Desktop' };
  assert.equal(describeVoiceSynthesis(selection, completed).status, 'applied');
  assert.equal(describeVoiceSynthesis(selection, { ...completed, provider: 'fake' }).reason, 'voice_target_mismatch');
  assert.equal(describeVoiceSynthesis(selection, { ...completed, actualVoice: 'other' }).reason, 'voice_identity_mismatch');
  assert.equal(describeVoiceSynthesis(selection, { completed: true }).status, 'unverified');
  assert.equal(describeVoiceSynthesis(selection, { error: { code: 'tts_timeout' } }).status, 'failed');
});
