# Deploy on Kubernetes

Use this guide to get DeltaLLM running from a released Helm chart. The evaluation setup is useful for learning and testing. A production setup needs external PostgreSQL and Redis services, managed secrets, backups, and monitoring.

## Before you start

You need:

- Kubernetes 1.24 or later
- Helm 3.10 or later
- `kubectl` access to the cluster
- the chart version from the DeltaLLM release you plan to install

Create the following Kubernetes Secrets with your normal secret-management process:

- `deltallm-app-secrets`, containing `master-key` and `salt-key`
- `deltallm-bootstrap-admin`, containing `PLATFORM_BOOTSTRAP_ADMIN_EMAIL` and `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD`

Use unique, strong values. Do not put secret values in Helm commands, shell history, or values files.

## Try DeltaLLM in an evaluation environment

The evaluation values file includes PostgreSQL and Redis. Do not use it for production.

```bash
helm repo add deltallm https://deltawi.github.io/deltallm
helm repo update

helm install deltallm deltallm/deltallm \
  --version <chart-version> \
  --namespace deltallm \
  --create-namespace \
  -f https://deltawi.github.io/deltallm/values-eval-<chart-version>.yaml \
  --set secret.existingSecret=deltallm-app-secrets \
  --set-string 'envFrom[0].secretRef.name=deltallm-bootstrap-admin'
```

Keep `<chart-version>` pinned to a version named in the release notes. This makes later upgrades predictable.

## Prepare a production install

Start with the matching production values file from the release. Configure it to use:

- externally managed PostgreSQL and Redis services
- existing Kubernetes Secrets for all credentials
- more than one application replica
- resource requests and limits
- an ingress with TLS
- private access to readiness and metrics endpoints
- backups and alerts that you have tested

Run database migrations as a separate, one-time job before application replicas start. See [Run database migrations](../deployment/database-migrations.md).

!!! warning "Do not copy the evaluation setup into production"
    The bundled database and cache are designed for evaluation. They do not provide the availability, backup, or recovery controls expected in production.

## Check the installation

First, confirm that the pods become ready:

```bash
kubectl get pods -n deltallm
```

Then open a temporary local connection:

```bash
kubectl port-forward -n deltallm svc/deltallm 4000:4000
```

In another terminal, check the public liveness endpoint:

```bash
curl http://localhost:4000/health/liveliness
```

Sign in with the account stored in `deltallm-bootstrap-admin`. Add a model, create an application key, and send one test request before exposing the service to users.

## Next steps

- Complete the [production checklist](../deployment/production-checklist.md).
- Set up [monitoring](monitoring.md).
- Read the [Kubernetes and Helm reference](../deployment/kubernetes.md) for all chart options and high-availability settings.
