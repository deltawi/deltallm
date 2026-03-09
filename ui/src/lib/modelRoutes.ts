export function encodeModelRouteId(deploymentId: string): string {
  return encodeURIComponent(deploymentId);
}

export function modelDetailPath(deploymentId: string): string {
  return `/models/${encodeModelRouteId(deploymentId)}`;
}

export function modelEditPath(deploymentId: string): string {
  return `${modelDetailPath(deploymentId)}/edit`;
}
