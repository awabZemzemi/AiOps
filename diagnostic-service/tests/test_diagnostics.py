import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import ReadTimeoutError

from diagnostics import MAX_EVENTS, MAX_LOG_BYTES, PodEvidenceError, collect_pod_evidence

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def make_pod():
    return client.V1Pod(
        metadata=client.V1ObjectMeta(name="demo", namespace="aiops", uid="pod-uid",
                                     creation_timestamp=NOW,
                                     owner_references=[client.V1OwnerReference(
                                         api_version="apps/v1", kind="ReplicaSet", name="demo-rs", uid="rs-uid")]),
        spec=client.V1PodSpec(
            containers=[client.V1Container(
                name="app", image="shop:v1",
                env=[client.V1EnvVar(name="PASSWORD", value="never-print-me")],
                resources=client.V1ResourceRequirements(requests={"cpu": "100m", "memory": "128Mi"},
                                                         limits={"cpu": "500m", "memory": "256Mi"}),
                readiness_probe=client.V1Probe(http_get=client.V1HTTPGetAction(
                    path="/health", port=8000, http_headers=[client.V1HTTPHeader(
                        name="Authorization", value="Bearer never-print-me")])),
                liveness_probe=client.V1Probe(_exec=client.V1ExecAction(command=["check", "never-print-me"])),
                startup_probe=client.V1Probe(tcp_socket=client.V1TCPSocketAction(port=8000)),
            )], restart_policy="Always", service_account_name="default", node_name="kind-node",
            volumes=[client.V1Volume(name="credentials", secret=client.V1SecretVolumeSource(
                secret_name="private-secret-name"))]),
        status=client.V1PodStatus(phase="Running", pod_ip="10.0.0.1", conditions=[
            client.V1PodCondition(type="Ready", status="True", last_transition_time=NOW)],
            container_statuses=[client.V1ContainerStatus(
                name="app", image="shop:v1", image_id="sha256:123", ready=True, restart_count=0,
                state=client.V1ContainerState(running=client.V1ContainerStateRunning(started_at=NOW)),
                last_state=client.V1ContainerState())]),
    )


def event(uid="pod-uid"):
    return client.CoreV1Event(
        metadata=client.V1ObjectMeta(name="event"),
        involved_object=client.V1ObjectReference(kind="Pod", name="demo", uid=uid),
        type="Normal", reason="Started", message="Container started", count=1,
        first_timestamp=NOW, last_timestamp=NOW)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.pod = make_pod()
        self.core = Mock(spec=client.CoreV1Api)
        self.metrics = Mock(spec=client.CustomObjectsApi)
        self.core.read_namespaced_pod.return_value = self.pod
        self.core.read_namespaced_pod_log.return_value = "2026-10-17T00:00:00Z started\n"
        self.core.list_namespaced_event.return_value = client.CoreV1EventList(
            items=[event(), event("old-pod-uid")], metadata=client.V1ListMeta())
        self.metrics.get_namespaced_custom_object.return_value = {
            "timestamp": "2026-10-17T00:00:00Z", "window": "15s",
            "containers": [{"name": "app", "usage": {"cpu": "120000n", "memory": "20Mi"}}]}

    def collect(self, **options):
        return collect_pod_evidence("aiops", "demo", core_api=self.core, metrics_api=self.metrics, **options)

    def test_healthy_pod_and_security(self):
        report = self.collect()
        encoded = json.dumps(report)
        self.assertNotIn("never-print-me", encoded)
        self.assertNotIn("private-secret-name", encoded)
        self.assertEqual(report["pod"]["volumes"], [{"name": "credentials", "types": ["secret"]}])
        self.assertEqual(report["pod"]["node_name"], "kind-node")
        self.assertEqual(report["pod"]["creation_timestamp"], NOW.isoformat())
        container = report["containers"][0]
        self.assertEqual(container["state"]["state"], "Running")
        self.assertEqual(container["resources"]["requests"]["cpu"], "100m")
        self.assertEqual(container["environment_variable_names"], ["PASSWORD"])
        self.assertEqual(report["metrics"]["containers"][0]["cpu"], "120000n")
        self.assertEqual(len(report["events"]), 1)
        self.assertEqual(report["collection_errors"], [])
        # Serializing/redacting must not mutate the original Kubernetes objects.
        self.assertEqual(self.pod.spec.containers[0].readiness_probe.http_get.http_headers[0].value,
                         "Bearer never-print-me")
        for call in self.core.read_namespaced_pod_log.call_args_list:
            self.assertEqual(call.kwargs["tail_lines"], 100)
            self.assertTrue(call.kwargs["timestamps"])
            self.assertFalse(call.kwargs["follow"])
            self.assertEqual(call.kwargs["limit_bytes"], MAX_LOG_BYTES)
            self.assertEqual(call.kwargs["_request_timeout"], (3, 10))
        self.assertIn("involvedObject.uid=pod-uid", self.core.list_namespaced_event.call_args.kwargs["field_selector"])
        self.assertEqual(self.core.list_namespaced_event.call_args.kwargs["limit"], MAX_EVENTS)

    def test_crash_loop(self):
        status = self.pod.status.container_statuses[0]
        status.state = client.V1ContainerState(waiting=client.V1ContainerStateWaiting(
            reason="CrashLoopBackOff", message="Back-off restarting"))
        status.restart_count = 7
        status.ready = False
        report = self.collect()
        container = report["containers"][0]
        self.assertEqual(container["state"]["reason"], "CrashLoopBackOff")
        self.assertEqual(container["restart_count"], 7)
        self.assertFalse(container["ready"])

    def test_previous_oom_killed(self):
        self.pod.status.container_statuses[0].last_state = client.V1ContainerState(
            terminated=client.V1ContainerStateTerminated(exit_code=137, reason="OOMKilled",
                                                        started_at=NOW, finished_at=NOW))
        previous = self.collect()["containers"][0]["previous_termination"]
        self.assertEqual(previous["reason"], "OOMKilled")
        self.assertEqual(previous["exit_code"], 137)
        self.assertEqual(previous["finished_at"], NOW.isoformat())

    def test_missing_previous_logs(self):
        def logs(**kwargs):
            if kwargs["previous"]:
                raise ApiException(status=400, reason="previous container does not exist", http_resp=None)
            return "current logs\n"
        self.core.read_namespaced_pod_log.side_effect = logs
        report = self.collect()
        self.assertIsNone(report["logs"]["app"]["previous"])
        self.assertEqual(report["logs"]["app"]["current"], "current logs\n")
        self.assertEqual(report["collection_errors"][0]["operation"], "logs.previous")
        self.assertEqual(report["collection_errors"][0]["container"], "app")
        self.assertIsNotNone(report["metrics"])

    def test_unavailable_metrics(self):
        for status in (403, 404, 503):
            with self.subTest(status=status):
                self.metrics.get_namespaced_custom_object.side_effect = ApiException(status=status)
                report = self.collect()
                self.assertIsNone(report["metrics"])
                self.assertEqual(report["collection_errors"][0]["operation"], "metrics")
                self.assertEqual(report["pod"]["name"], "demo")

    def test_multiple_containers_including_init_and_ephemeral(self):
        self.pod.spec.containers.append(client.V1Container(name="sidecar", image="helper:v1"))
        self.pod.spec.init_containers = [client.V1Container(name="setup", image="setup:v1")]
        self.pod.spec.ephemeral_containers = [client.V1EphemeralContainer(name="debug", image="debug:v1")]
        report = self.collect()
        self.assertEqual(len(report["containers"]), 4)
        self.assertEqual(set(report["logs"]), {"app", "sidecar", "setup", "debug"})
        self.assertEqual(self.core.read_namespaced_pod_log.call_count, 8)
        self.assertIsNone(report["containers"][1]["ready"])
        self.assertIsNone(report["containers"][1]["restart_count"])
        self.assertEqual(report["containers"][2]["type"], "init")
        self.assertEqual(report["containers"][3]["type"], "ephemeral")

    def test_missing_or_inaccessible_target(self):
        for status in (404, 403, 401):
            with self.subTest(status=status):
                self.core.read_namespaced_pod.side_effect = ApiException(status=status, reason="sensitive body")
                with self.assertRaises(PodEvidenceError) as error:
                    self.collect()
                self.assertEqual(error.exception.status, status)
                self.assertIn("aiops/demo", str(error.exception))
                self.assertNotIn("sensitive body", str(error.exception))
                self.core.read_namespaced_pod_log.assert_not_called()

    def test_events_and_log_network_failures_are_partial(self):
        self.core.list_namespaced_event.side_effect = ApiException(status=403)
        self.core.read_namespaced_pod_log.side_effect = ReadTimeoutError(None, "/", "timed out")
        report = self.collect()
        self.assertEqual(report["events"], [])
        self.assertEqual(report["logs"]["app"], {"current": None, "previous": None})
        self.assertEqual(len(report["collection_errors"]), 3)
        self.assertIsNotNone(report["metrics"])

    def test_event_limit_and_series_timestamps(self):
        current_event = event()
        current_event.first_timestamp = None
        current_event.last_timestamp = None
        current_event.event_time = NOW
        current_event.series = client.CoreV1EventSeries(count=8, last_observed_time=NOW)
        self.core.list_namespaced_event.return_value = client.CoreV1EventList(
            items=[current_event] * (MAX_EVENTS + 1), metadata=client.V1ListMeta(_continue="next"))
        report = self.collect()
        self.assertEqual(len(report["events"]), MAX_EVENTS)
        self.assertEqual(report["events"][0]["count"], 8)
        self.assertEqual(report["events"][0]["last_observed_at"], NOW.isoformat())
        self.assertEqual(report["collection_errors"][0]["error_type"], "CollectionLimit")

    def test_log_bounds_and_validation(self):
        self.core.read_namespaced_pod_log.return_value = "a" * (MAX_LOG_BYTES * 2)
        report = self.collect(tail_lines=20)
        self.assertEqual(len(report["logs"]["app"]["current"]), MAX_LOG_BYTES)
        self.assertEqual(self.core.read_namespaced_pod_log.call_args.kwargs["tail_lines"], 20)
        for lines in (0, -1, 1001, True, "100"):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                self.collect(tail_lines=lines)


if __name__ == "__main__":
    unittest.main()
