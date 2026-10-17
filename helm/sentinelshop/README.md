# SentinelShop Helm chart

Run these commands from the repository root. Helm packages the same five application Deployments, seven internal Services, two ConfigMaps, and persistent Redis StatefulSet as `k8s/`.

## Deploy on kind

The default application tag is `latest`, matching the local images loaded into `sentinelops`. Build and load them if needed:

```bash
for service in frontend catalog inventory orders checkout; do
  docker build -t "sentinelshop-$service:latest" "app/$service"
  kind load docker-image "sentinelshop-$service:latest" --name sentinelops
done
```

Redis uses `redis:7.4-alpine`. Nodes can pull it, or you can preload it:

```bash
docker pull redis:7.4-alpine
kind load docker-image redis:7.4-alpine --name sentinelops
```

Install or update the release:

```bash
helm upgrade --install sentinelshop ./helm/sentinelshop \
  --kube-context kind-sentinelops \
  --namespace aiops --create-namespace \
  --wait --timeout 3m
kubectl --context kind-sentinelops -n aiops \
  port-forward service/sentinelshop-frontend 8080:80
```

Open http://localhost:8080. Verify products with `curl http://localhost:8080/api/products`.

Resources are prefixed with the release name, and selectors include the release identity. An existing raw-manifest deployment can coexist, but this chart creates a separate Redis instance and fresh stock; it does not migrate its data or adopt its resources. The original `k8s/` files remain available independently. Namespace creation is handled by `--create-namespace`.

## Configure

Use `-f my-values.yaml` with the install command or `--set` overrides. See `values.yaml` for defaults:

| Value | Purpose |
| --- | --- |
| `clusterDomain` | Kubernetes DNS domain (default `cluster.local`); Nginx uses full Service DNS names. |
| `image.tag` | Default tag for all five application images (`latest`). |
| `image.pullPolicy` | Defaults to `IfNotPresent`, so loaded kind images are used. |
| `components.<name>.image.repository` | Local image name or registry repository. |
| `components.<name>.image.tag` | Optional per-component tag overriding `image.tag`. |
| `components.<name>.replicaCount` | Desired application replicas (default 1). |
| `components.<name>.resources` | CPU and memory requests/limits. |
| `imagePullSecrets` | List of `{name: secret-name}` entries for private registries. |
| `redis.image` | Redis repository and tag. |
| `redis.resources` | Redis CPU and memory requests/limits. |
| `redis.persistence.size` | Initial PVC size (default `1Gi`). |
| `redis.persistence.storageClass` | `null` uses the cluster default; a name selects a class; `""` disables dynamic class selection. |

For example, after building/loading `v2` images:

```bash
helm upgrade --install sentinelshop ./helm/sentinelshop \
  --kube-context kind-sentinelops -n aiops --create-namespace \
  --set image.tag=v2 --set components.catalog.replicaCount=2 \
  --wait --timeout 3m
```

Use a new image tag for each build. Loading a changed image under the same tag does not trigger a rollout. ConfigMap template changes automatically change Pod checksums and trigger application rollouts. Probes retain the raw manifests' behavior: inventory readiness checks Redis; the other API readiness probes only check their own process.

Redis stays at one replica because replication is not configured. A default StorageClass is needed unless you select another class or provide matching storage. PVC template size/class changes on an existing StatefulSet need a separate storage migration; these settings are intended for initial installation.

## Inspect, validate, and remove

```bash
helm lint ./helm/sentinelshop
helm template sentinelshop ./helm/sentinelshop -n aiops
python3 -m unittest discover -s helm/sentinelshop/tests
kubectl -n aiops get pods,services,pvc \
  -l app.kubernetes.io/instance=sentinelshop
helm uninstall sentinelshop -n aiops
```

The tests require Helm, Python 3, and PyYAML. Uninstall removes chart resources but retains the Redis PVC and namespace. Reinstalling the same release in the same namespace reuses its PVC. To permanently erase this release's stock after uninstall:

```bash
kubectl -n aiops delete pvc redis-data-sentinelshop-redis-0
```

Deleting the namespace also deletes its PVCs. Local kind storage does not survive deletion of the kind cluster.
