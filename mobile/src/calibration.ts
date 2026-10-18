// No native APIs: shared decisions for the live calibration controls and tests.
export function calibrationCommand(text: string): 'start' | 'finish' | null {
  const normalized = text.toLowerCase().replace(/[^a-z\s]/g, '').replace(/\s+/g, ' ').trim();
  if (/^(start|begin|continue|resume) (calibration|mapping)$/.test(normalized)
      || normalized === 'map my apartment') return 'start';
  if (/^(finish|stop|end) (calibration|mapping)$/.test(normalized)) return 'finish';
  return null;
}

/** One local camera run, independent of the apartment's persistent map. */
export const observationSession = () => `phone_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;

export function observationFields(room?: string, sessionId?: string): Record<string, string> {
  return { ...(room ? { room } : {}), ...(sessionId ? { session_id: sessionId } : {}) };
}

/** Finish the current frame before answering; only actual navigation pauses map learning. */
export async function prepareLearningReply<T extends { intent: string }>(
  pending: Promise<unknown> | null, chat: () => Promise<T>, finish: () => Promise<boolean>,
): Promise<T> {
  await pending?.catch(() => undefined);
  const reply = await chat();
  if (reply.intent === 'find' && !await finish()) throw new Error('Could not pause apartment learning.');
  return reply;
}
