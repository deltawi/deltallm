import type { SessionInfo } from './authTypes';
import { classifySessionCheckError, isValidSessionPayload } from './authSession';

interface SessionClient {
  me: () => Promise<unknown>;
  masterLogin: (key: string) => Promise<unknown>;
}

function validatedSession(value: unknown): SessionInfo {
  if (!isValidSessionPayload(value)) throw new Error('Invalid session verification response');
  return value;
}

export async function resolveSession(
  client: SessionClient,
  legacyKey: string | null,
  externalConsole: boolean,
): Promise<SessionInfo> {
  let session: SessionInfo;
  try {
    session = validatedSession(await client.me());
  } catch (error) {
    if (classifySessionCheckError(error).kind !== 'anonymous') throw error;
    session = { authenticated: false };
  }
  if (session.authenticated || externalConsole || !legacyKey) return session;
  await client.masterLogin(legacyKey);
  return validatedSession(await client.me());
}
