# Kubernetes Deployment Assets

This directory contains Kubernetes deployment assets for DeltaLLM.

## Helm Chart

The Helm chart lives at:

```text
deploy/kubernetes/helm
```

Use the published chart repository for normal installs. Use this local chart path when developing chart changes or testing unreleased chart behavior.

```bash
helm dependency build deploy/kubernetes/helm
helm lint deploy/kubernetes/helm \
  -f deploy/kubernetes/helm/values-eval.yaml \
  --set secret.values.masterKey=sk-validation-master-1234567890A1 \
  --set secret.values.saltKey=validation-salt-0123456789abcdef0123456789abcdef
helm template deltallm deploy/kubernetes/helm \
  -f deploy/kubernetes/helm/values-eval.yaml \
  --set secret.values.masterKey=sk-validation-master-1234567890A1 \
  --set secret.values.saltKey=validation-salt-0123456789abcdef0123456789abcdef
```

See the full Kubernetes deployment guide in `docs/deployment/kubernetes.md`.
