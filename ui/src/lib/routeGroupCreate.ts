import { routeGroups } from './api';
import { mutationOutcome } from './mutationOutcome';
import { defaultSimplePolicy } from './routeGroupRouting';

export interface GroupSetup {
  total: number;
  added: number;
  /** The step that failed, or null when the group is ready. */
  failed: 'models' | 'routing' | null;
  error: string | null;
  warnings: string[];
}

function errorText(error: unknown): string {
  const text = error instanceof Error && error.message ? error.message.trim() : 'The request failed.';
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

/** Adds the picked models to a new group, then publishes the default routing. Stops at the first failure. */
export async function setUpGroupModels(routeGroupId: string, deploymentIds: readonly string[], signal: AbortSignal): Promise<GroupSetup> {
  const setup: GroupSetup = { total: deploymentIds.length, added: 0, failed: null, error: null, warnings: [] };
  try {
    for (const deploymentId of deploymentIds) {
      const result = await routeGroups.upsertMember(routeGroupId, { deployment_id: deploymentId, enabled: true }, signal);
      setup.warnings.push(...result.warnings);
      setup.added += 1;
    }
  } catch (error: unknown) {
    return { ...setup, failed: 'models', error: errorText(error) };
  }
  try {
    const result = await routeGroups.publishPolicy(routeGroupId, defaultSimplePolicy(), signal);
    setup.warnings.push(...result.warnings);
  } catch (error: unknown) {
    return { ...setup, failed: 'routing', error: errorText(error) };
  }
  return setup;
}

export interface CreatedGroupToast {
  tone: 'success' | 'info' | 'error';
  title: string;
  message: string;
}

/** The toast after a group is created, with or without picked models. */
export function createdGroupToast(groupKey: string, createWarnings: readonly string[], setup: GroupSetup | null): CreatedGroupToast {
  if (setup?.failed === 'models') {
    return {
      tone: 'error',
      title: 'Model group created with problems',
      message: `${setup.added} of ${setup.total} models were added. ${setup.error} Add the other models on the Models tab.`,
    };
  }
  if (setup?.failed === 'routing') {
    return {
      tone: 'error',
      title: 'Model group created with problems',
      message: `The models were added, but the routing was not saved. ${setup.error} Save it on the Routing tab.`,
    };
  }
  const count = setup?.added ?? 0;
  const outcome = mutationOutcome(
    count > 0
      ? `"${groupKey}" is ready. It spreads requests evenly across ${count} ${count === 1 ? 'model' : 'models'}.`
      : `"${groupKey}" is ready for configuration.`,
    [...createWarnings, ...(setup?.warnings ?? [])],
  );
  return { tone: outcome.tone, title: 'Model group created', message: outcome.message };
}
