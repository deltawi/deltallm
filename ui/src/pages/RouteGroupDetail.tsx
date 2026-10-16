import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Code2, FileText, GitBranch, Server, Settings, Workflow } from 'lucide-react';
import Button from '../components/Button';
import { EntityAvatar, IconTabs, PageShell } from '../components/admin/shells';
import RouteGroupMeta, { RouteGroupModeBadge, type RouteGroupTabId } from '../components/route-groups/RouteGroupMeta';
import RouteGroupModelsTab from '../components/route-groups/RouteGroupModelsTab';
import RouteGroupPromptTab from '../components/route-groups/RouteGroupPromptTab';
import RouteGroupRoutingTab from '../components/route-groups/RouteGroupRoutingTab';
import RouteGroupSettingsTab from '../components/route-groups/RouteGroupSettingsTab';
import UseGroupDrawer, { type BoundPrompt } from '../components/route-groups/UseGroupDrawer';
import { Skeleton } from '../components/usage/ReportBits';
import { ApiError, promptRegistry, routeGroups } from '../lib/api';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession, resolveUiAccess } from '../lib/authorization';
import { useApi } from '../lib/hooks';
import { navTrail } from '../lib/navigation';
import { sharingSummary } from '../lib/routeGroupSummary';
import { useManagedAssetAudienceOptions } from '../lib/useManagedAssetAudienceOptions';
import { useRouteGroupMutationScope } from '../lib/useRouteGroupMutationScope';

const TABS = [
  { id: 'models', label: 'Models', icon: Server },
  { id: 'routing', label: 'Routing', icon: GitBranch },
  { id: 'prompt', label: 'Prompt', icon: FileText },
  { id: 'settings', label: 'Settings', icon: Settings },
] as const;

const TABS_ID = 'model-group-tabs';

function loadErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return 'You do not have permission to view this model group.';
  if (error instanceof ApiError && error.status === 404) return 'Model group not found.';
  return 'Failed to load route group details.';
}

export default function RouteGroupDetail({ routeGroupId }: { routeGroupId: string }) {
  const mutations = useRouteGroupMutationScope();
  const navigate = useNavigate();
  const { authMode, session } = useAuth();
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const audiences = useManagedAssetAudienceOptions(session, isPlatformAdmin);
  const [activeTab, setActiveTab] = useState<RouteGroupTabId>('models');
  // A tab loads when it is first opened, then stays mounted so unsaved edits survive a tab switch.
  const [visited, setVisited] = useState<ReadonlySet<RouteGroupTabId>>(() => new Set(['models']));
  const [using, setUsing] = useState(false);
  const [addRequest, setAddRequest] = useState(0);

  const detail = useApi((signal) => routeGroups.get(routeGroupId, signal), [routeGroupId]);
  const groupKey = detail.data?.group.group_key ?? '';
  const resolution = useApi(
    (signal) => (isPlatformAdmin && groupKey ? promptRegistry.previewResolution({ route_group_key: groupKey }, signal) : Promise.resolve(null)),
    [groupKey, isPlatformAdmin],
  );

  const openTab = (tab: RouteGroupTabId) => {
    setActiveTab(tab);
    setVisited((current) => (current.has(tab) ? current : new Set([...current, tab])));
  };
  const openAddModel = () => {
    setAddRequest((count) => count + 1);
    openTab('models');
  };

  const breadcrumbs = navTrail('/route-groups');
  if (!detail.data?.group) {
    const failed = !!detail.error && !detail.loading;
    return (
      <PageShell breadcrumbs={breadcrumbs} avatar={<EntityAvatar icon={Workflow} />} title="Model group">
        {failed ? (
          <div className="space-y-3">
            <div role="alert" className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{loadErrorMessage(detail.error)}</div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={detail.refetch}>Retry</Button>
              <Button size="sm" variant="ghost" onClick={() => navigate('/route-groups')}>Back to Route Groups</Button>
            </div>
          </div>
        ) : (
          <div role="status" aria-label="Loading model group" className="max-w-4xl space-y-3 rounded-xl border border-gray-200 bg-white px-4 py-4 shadow-sm">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-3 w-80" />
            <Skeleton className="h-24" />
          </div>
        )}
      </PageShell>
    );
  }

  const { group, members, policy } = detail.data;
  const access = group.access;
  const canWrite = isPlatformAdmin || !access || access.capabilities.write;
  const canDelete = isPlatformAdmin || !access || access.capabilities.delete;
  const canSimulate = resolveUiAccess(authMode, session).route_groups;
  const winner = resolution.data?.winner ?? null;
  const prompt: BoundPrompt | null = typeof winner?.template_key === 'string'
    ? { templateKey: winner.template_key, label: typeof winner.label === 'string' ? winner.label : null }
    : null;
  const sharing = access ? sharingSummary(access, audiences.teamOptions, audiences.organizationOptions) : null;
  // Prompt bindings are managed by platform admins only.
  const tabs = TABS.filter((tab) => tab.id !== 'prompt' || isPlatformAdmin);
  const panel = (id: RouteGroupTabId) => (visited.has(id) ? renderTab(id) : null);

  function renderTab(id: RouteGroupTabId) {
    switch (id) {
      case 'models':
        return (
          <RouteGroupModelsTab group={group} members={members} policy={policy} canWrite={canWrite} mutations={mutations}
            addRequest={addRequest} onChanged={detail.refetch} onOpenRouting={() => openTab('routing')} />
        );
      case 'routing':
        return (
          <RouteGroupRoutingTab group={group} members={members} canWrite={canWrite} canSimulate={canSimulate} mutations={mutations}
            promptRef={prompt ? { template_key: prompt.templateKey, ...(prompt.label ? { label: prompt.label } : {}) } : null}
            onPublished={detail.refetch} onAddModel={openAddModel} />
        );
      case 'prompt':
        return <RouteGroupPromptTab groupKey={group.group_key} mutations={mutations} onChanged={resolution.refetch} />;
      case 'settings':
        return (
          <RouteGroupSettingsTab group={group} memberCount={members.length} sharing={sharing} canWrite={canWrite} canDelete={canDelete}
            isPlatformAdmin={isPlatformAdmin} audiences={audiences} mutations={mutations}
            onSaved={detail.refetch} onDeleted={() => navigate('/route-groups')} />
        );
    }
  }

  return (
    <>
      <PageShell
        breadcrumbs={[...breadcrumbs, { label: group.name || group.group_key }]}
        avatar={<EntityAvatar icon={Workflow} />}
        title={group.name || group.group_key}
        badges={<RouteGroupModeBadge mode={group.mode} />}
        meta={<RouteGroupMeta group={group} members={members} policy={policy} promptKey={prompt?.templateKey ?? null} sharing={sharing} onOpenTab={openTab} />}
        actions={<Button size="sm" onClick={() => setUsing(true)}><Code2 aria-hidden="true" className="h-4 w-4" />Use this group</Button>}
        tabs={(
          <IconTabs id={TABS_ID} label="Model group sections" variant="header" active={activeTab} onChange={openTab}
            items={tabs.map((tab) => ({ ...tab, count: tab.id === 'models' ? members.length : undefined }))} />
        )}
      >
        {tabs.map(({ id }) => (
          <div key={id} role="tabpanel" id={`${TABS_ID}-panel-${id}`} aria-labelledby={`${TABS_ID}-tab-${id}`} hidden={activeTab !== id}>
            {panel(id)}
          </div>
        ))}
      </PageShell>
      {using ? <UseGroupDrawer group={group} prompt={prompt} onClose={() => setUsing(false)} /> : null}
    </>
  );
}
