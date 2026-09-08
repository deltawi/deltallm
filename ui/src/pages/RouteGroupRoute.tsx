import { Navigate, Link, useLocation, useParams } from 'react-router-dom';
import { ApiError, routeGroups } from '../lib/api';
import { useApi } from '../lib/hooks';
import { legacyRouteGroupKey, routeGroupDetailPath } from '../lib/routeGroupRoutes';
import RouteGroupDetail from './RouteGroupDetail';

function LegacyRouteGroup({ groupKey }: { groupKey: string }) {
  const resolution = useApi((signal) => routeGroups.resolveKey(groupKey, signal), [groupKey]);
  if (resolution.data) return <Navigate to={routeGroupDetailPath(resolution.data.route_group_id)} replace />;
  const message = resolution.error instanceof ApiError && resolution.error.status === 403
    ? 'You do not have permission to view this model group.'
    : resolution.error instanceof ApiError && resolution.error.status === 404
      ? 'Model group not found.'
      : resolution.error ? 'Could not load this model group.' : 'Loading model group…';
  return (
    <div className="space-y-4 p-6">
      <Link to="/route-groups" className="text-sm text-brand-primary">Back to Model Groups</Link>
      <p role={resolution.error ? 'alert' : 'status'}>{message}</p>
      {!!resolution.error && <button onClick={resolution.refetch} className="rounded border px-3 py-2">Retry</button>}
    </div>
  );
}

export default function RouteGroupRoute() {
  const { routeGroupId } = useParams<{ routeGroupId: string }>();
  const { pathname } = useLocation();
  if (routeGroupId) return <RouteGroupDetail key={routeGroupId} routeGroupId={routeGroupId} />;
  const groupKey = legacyRouteGroupKey(pathname);
  if (groupKey === null) return <p role="alert" className="p-6">Invalid model group link.</p>;
  return <LegacyRouteGroup key={groupKey} groupKey={groupKey} />;
}
