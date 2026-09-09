import { useAuth } from '../../lib/auth';
import { resolveUiAccess } from '../../lib/authorization';
import SelectorCostPanel from './SelectorCostPanel';
import SelectorEvaluationPanel from './SelectorEvaluationPanel';

export default function SelectorTools({ routeGroupId, groupKey, policy }: {
  routeGroupId: string; groupKey: string; policy: Record<string, unknown> | null;
}) {
  const { authMode, session } = useAuth();
  const access = resolveUiAccess(authMode, session);
  const scope = JSON.stringify([authMode, session?.account_id, session?.effective_permissions, access, routeGroupId]);
  return <div key={scope} className="space-y-3">
    <SelectorEvaluationPanel policy={policy} scope={scope} allowed={access.route_groups} />
    <SelectorCostPanel scope={scope} modelGroup={groupKey} allowed={access.usage} />
  </div>;
}
