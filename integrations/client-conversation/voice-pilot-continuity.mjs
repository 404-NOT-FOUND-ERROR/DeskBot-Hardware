export const promptComposer = 'apps/deskbot-service/src/prompt-composer.mjs';
export const promptComposerSha256 = 'f8526fe4c7b18e5533c9c6554b3f01dae349a014203dbcd5aeb922f3be1c3172';

const original = "  if (!/(刚才|上次|之前|继续|接着|再说|再来|还是|那个|这件|你说的|记得|刚说|刚问|换个|然后呢)/.test(userText)) return [];";
const replacement = [
  "  // Ordinary follow-ups also need short-term context; spoken questions often",
  "  // omit explicit cues such as 'earlier'. Keep two recent turns in that case.",
  original.replace('return [];', "return conversationTopic(userText) === 'general' ? entries.slice(-4) : [];"),
].join('\n');

export function applyVoicePilotContinuity(source) {
  if (source.split(original).length !== 2) throw new Error('voice_pilot_context_patch_mismatch');
  return source.replace(original,replacement);
}
