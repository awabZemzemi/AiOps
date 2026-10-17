"""Render the chart and verify routing, isolation, configuration, and storage."""
from pathlib import Path
import subprocess
import unittest

import yaml

CHART = Path(__file__).resolve().parents[1]


def render(release="demo", *options):
    output = subprocess.check_output(
        ["helm", "template", release, str(CHART), "--namespace", "lab", *options],
        text=True,
    )
    return list(yaml.safe_load_all(output))


class ChartTests(unittest.TestCase):
    def test_default_wiring_and_storage(self):
        docs = render()
        self.assertEqual(len(docs), 15)
        resources = {(d["kind"], d["metadata"]["name"]): d for d in docs}
        workloads = [d for d in docs if d["kind"] in ("Deployment", "StatefulSet")]
        self.assertEqual(len(workloads), 6)
        for doc in docs:
            self.assertEqual(doc["metadata"]["namespace"], "lab")
        for workload in workloads:
            pod = workload["spec"]["template"]
            labels = pod["metadata"]["labels"]
            for key, value in workload["spec"]["selector"]["matchLabels"].items():
                self.assertEqual(labels[key], value)
            for container in pod["spec"]["containers"]:
                for env in container.get("env", []):
                    ref = env["valueFrom"]["configMapKeyRef"]
                    config = resources[("ConfigMap", ref["name"])]
                    self.assertIn(ref["key"], config["data"])
                self.assertEqual(container["imagePullPolicy"], "IfNotPresent")
        for service in [d for d in docs if d["kind"] == "Service"]:
            matches = [w for w in workloads if all(
                w["spec"]["template"]["metadata"]["labels"].get(k) == v
                for k, v in service["spec"]["selector"].items()
            )]
            self.assertEqual(len(matches), 1)
            port = service["spec"]["ports"][0]["targetPort"]
            ports = matches[0]["spec"]["template"]["spec"]["containers"][0]["ports"]
            self.assertIn(port, [p["name"] for p in ports])
        config = resources[("ConfigMap", "demo-app-config")]["data"]
        self.assertEqual(config["INVENTORY_URL"], "http://demo-inventory:8000")
        self.assertEqual(config["ORDERS_URL"], "http://demo-orders:8000")
        self.assertEqual(config["REDIS_HOST"], "demo-redis")
        frontend = resources[("ConfigMap", "demo-frontend-config")]["data"]
        self.assertIn("demo-catalog.lab.svc.cluster.local:8000", frontend["default.conf.template"])
        self.assertIn("demo-checkout.lab.svc.cluster.local:8000", frontend["default.conf.template"])
        redis = resources[("StatefulSet", "demo-redis")]["spec"]
        self.assertEqual(redis["replicas"], 1)
        self.assertEqual(redis["serviceName"], "demo-redis-headless")
        pvc = redis["volumeClaimTemplates"][0]["spec"]
        self.assertNotIn("storageClassName", pvc)
        self.assertEqual(pvc["resources"]["requests"]["storage"], "1Gi")
        inventory = resources[("Deployment", "demo-inventory")]
        probe = inventory["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]
        self.assertEqual(probe["httpGet"]["path"], "/redis-health")

    def test_overrides(self):
        docs = render("custom", "--set", "image.tag=v2", "--set",
                      "components.catalog.image.tag=v3", "--set", "clusterDomain=custom.local", "--set",
                      "components.catalog.replicaCount=2", "--set",
                      "redis.persistence.storageClass=fast", "--set",
                      "redis.persistence.size=2Gi", "--set",
                      "imagePullSecrets[0].name=registry")
        frontend = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "custom-frontend-config")
        self.assertIn("custom-catalog.lab.svc.custom.local:8000", frontend["data"]["default.conf.template"])
        self.assertIn("custom-checkout.lab.svc.custom.local:8000", frontend["data"]["default.conf.template"])
        for doc in docs:
            if doc["kind"] == "Deployment":
                spec = doc["spec"]
                container = spec["template"]["spec"]["containers"][0]
                catalog = container["name"] == "catalog"
                self.assertTrue(container["image"].endswith(":v3" if catalog else ":v2"))
                self.assertEqual(spec["replicas"], 2 if catalog else 1)
                self.assertEqual(spec["template"]["spec"]["imagePullSecrets"], [{"name": "registry"}])
            if doc["kind"] == "StatefulSet":
                pvc = doc["spec"]["volumeClaimTemplates"][0]["spec"]
                self.assertEqual(pvc["storageClassName"], "fast")
                self.assertEqual(pvc["resources"]["requests"]["storage"], "2Gi")

    def test_release_isolation(self):
        first, second = render("first"), render("second")
        self.assertFalse({d["metadata"]["name"] for d in first} &
                         {d["metadata"]["name"] for d in second})
        for service in [d for d in first if d["kind"] == "Service"]:
            for workload in [d for d in second if d["kind"] in ("Deployment", "StatefulSet")]:
                labels = workload["spec"]["template"]["metadata"]["labels"]
                self.assertFalse(all(labels.get(k) == v for k, v in service["spec"]["selector"].items()))
