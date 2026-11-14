"""Offline HTTP and async graph checks: python -m unittest test_backend -v."""

import asyncio
import json
import os
import stat
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict
from langgraph.errors import GraphRecursionError

from backend.main import InvestigateRequest, InvestigateResponse, app, investigate
from backend.logging_setup import configure_logging, log_event, redact
from starlette.requests import Request
from diagnostics import PodEvidenceError


class PrivateLogTests(unittest.TestCase):
    def setUp(self):
        self.log_directory = Path(self.enterContext(TemporaryDirectory(prefix="aiops-logs-")))
        self.enterContext(patch.dict(os.environ, {"AIOPS_LOG_DIR": str(self.log_directory)}))

    def entries(self, name):
        return [json.loads(line) for line in (self.log_directory / f"{name}.jsonl").read_text().splitlines()]


class BackendTests(PrivateLogTests):
    def setUp(self):
        super().setUp()
        self.graph = Mock()
        self.result = {"messages": [AIMessage(content="Pod is running.")]}
        self.failure = None

        async def stream(state, **kwargs):
            yield state
            yield self.result
            if self.failure:
                raise self.failure

        self.graph.astream.side_effect = stream
        self.enterContext(patch("backend.main.load_dotenv"))
        self.factory = self.enterContext(patch("backend.main.create_graph", return_value=self.graph))
        self.client = self.enterContext(TestClient(app))

    def test_graph_is_built_at_startup_and_reused(self):
        self.factory.assert_called_once_with()
        self.assertIs(app.state.agent, self.graph)
        self.graph.astream.assert_not_called()
        ids = []
        for message in ("Inspect demo in namespace aiops.", "Inspect other in namespace aiops."):
            response = self.client.post("/agent/investigate", json={"message": message})
            self.assertEqual(response.status_code, 200)
            ids.append(response.json()["investigation_id"])
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual([
            call.kwargs["config"]["metadata"]["investigation_id"]
            for call in self.graph.astream.call_args_list
        ], ids)
        self.factory.assert_called_once_with()
        self.assertEqual(self.graph.astream.call_count, 2)
        histories = [call.args[0] for call in self.graph.astream.call_args_list]
        self.assertTrue(all(set(state) == {"messages"} and len(state["messages"]) == 1 for state in histories))
        self.assertNotEqual(histories[0]["messages"][0].content, histories[1]["messages"][0].content)

    def test_post_streams_graph_and_returns_validated_final_answer(self):
        self.result = {"messages": [
            HumanMessage(content="Inspect demo."),
            ToolMessage(content="diagnostic evidence", tool_call_id="call-1"),
            AIMessage(content="Pod is running."),
        ]}
        response = self.client.post("/agent/investigate", json={"message": "  Inspect demo.  "})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["answer"], "Pod is running.")
        self.assertEqual(body["status"], "completed")
        self.assertEqual(InvestigateResponse.model_validate(body).investigation_id, UUID(body["investigation_id"]))
        self.graph.astream.assert_called_once()
        state = self.graph.astream.call_args.args[0]
        self.assertEqual(set(state), {"messages"})
        self.assertIsInstance(state["messages"][0], HumanMessage)
        self.assertEqual(state["messages"][0].content, "Inspect demo.")
        self.graph.invoke.assert_not_called()
        self.graph.ainvoke.assert_not_called()

    def test_metadata_uses_messages_and_measured_time_and_preserves_markdown(self):
        answer = "## Diagnosis\n\n**CrashLoopBackOff**\n\n```sh\nkubectl logs demo\n```\n\nTools used: invented_tool (999 calls).\n"
        calls = [
            {"name": "get_logs", "args": {}, "id": "logs-1"},
            {"name": "get_metrics", "args": {}, "id": "metrics-1"},
        ]
        self.result = {"messages": [
            HumanMessage(content="Inspect demo."),
            AIMessage(content="", tool_calls=calls),
            ToolMessage(content="logs", name="get_logs", tool_call_id="logs-1"),
            ToolMessage(content="metrics unavailable", name="get_metrics",
                        tool_call_id="metrics-1", status="error"),
            AIMessage(content="", tool_calls=[{"name": "get_logs", "args": {}, "id": "logs-2"}]),
            ToolMessage(content="more logs", name="get_logs", tool_call_id="logs-2"),
            AIMessage(content=answer, response_metadata={"model_name": "scripted-model"}),
        ]}
        with patch("backend.main.perf_counter", side_effect=[10, 10.125]):
            response = self.client.post("/agent/investigate", json={"message": "Inspect demo."})
        body = response.json()
        self.assertEqual(body["answer"], answer)
        self.assertEqual(body["metadata"], {
            "duration_ms": 125.0, "tools_used": ["get_logs", "get_metrics"], "tool_call_count": 3,
        })
        operations = self.entries("operations")
        self.assertEqual([entry["event"] for entry in operations], [
            "investigation_started", "investigation_completed",
        ])
        self.assertTrue(all(entry["investigation_id"] == body["investigation_id"] for entry in operations))
        self.assertEqual(operations[-1]["duration_ms"], 125.0)
        self.assertEqual(operations[-1]["tools_used"], ["get_logs", "get_metrics"])
        history = self.entries("messages")[0]
        self.assertEqual(history["messages"], messages_to_dict(self.result["messages"]))
        self.assertEqual(history["investigation_id"], body["investigation_id"])

    def test_no_tool_calls_are_inferred_from_answer(self):
        self.result = {"messages": [AIMessage(content="Used get_logs 42 times.")]}
        body = self.client.post("/agent/investigate", json={"message": "Inspect demo."}).json()
        self.assertEqual(body["metadata"]["tools_used"], [])
        self.assertEqual(body["metadata"]["tool_call_count"], 0)

    def test_full_history_is_redacted_before_persistence_without_mutating_answer(self):
        answer = "**Diagnosis**\npassword=answer-secret"
        self.result = {"messages": [
            HumanMessage(content="password=request-secret"),
            AIMessage(content="", tool_calls=[{
                "name": "get_logs", "id": "logs-1", "args": {"api_key": "argument-secret"},
            }], additional_kwargs={"headers": {"Authorization": "Bearer header-secret"}}),
            ToolMessage(content=json.dumps({"logs": "email=alice@example.com password=log-secret"}),
                        name="get_logs", tool_call_id="logs-1"),
            AIMessage(content=answer, usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
        ]}
        response = self.client.post("/agent/investigate", json={"message": "password=request-secret"})
        self.assertEqual(response.json()["answer"], answer)
        saved = (self.log_directory / "messages.jsonl").read_text()
        for secret in ("request-secret", "argument-secret", "header-secret", "alice@example.com", "log-secret", "answer-secret"):
            self.assertNotIn(secret, saved)
        history = self.entries("messages")[0]["messages"]
        self.assertEqual(len(history), 4)
        self.assertEqual(history[-1]["data"]["usage_metadata"]["total_tokens"], 15)
        self.assertEqual(self.result["messages"][-1].content, answer)

    def test_text_blocks_are_returned_as_answer_text(self):
        self.result = {"messages": [AIMessage(content=[
            {"type": "text", "text": "Pod is running."},
        ])]}
        response = self.client.post("/agent/investigate", json={"message": "Inspect demo."})
        body = response.json()
        self.assertEqual(body["answer"], "Pod is running.")
        self.assertEqual(body["status"], "completed")
        self.assertEqual(InvestigateResponse.model_validate(body).investigation_id, UUID(body["investigation_id"]))

    def test_invalid_requests_do_not_invoke_graph(self):
        for body in ({}, {"message": ""}, {"message": " \n\t "}, {"message": None},
                     {"message": 123}, {"message": True}, {"message": []}):
            with self.subTest(body=body):
                response = self.client.post("/agent/investigate", json=body)
                self.assertEqual(response.status_code, 422)
        self.graph.astream.assert_not_called()

    def test_recursion_limit_has_safe_service_unavailable_response(self):
        self.failure = GraphRecursionError("private investigation details")
        response = self.client.post("/agent/investigate", json={"message": "Inspect demo."})
        self.assertEqual(response.status_code, 503)
        self.assertIn("graph step limit", response.json()["answer"])
        self.assertEqual(response.json()["status"], "failed")
        self.assertNotIn("private", response.text)

    def test_unexpected_errors_do_not_expose_details(self):
        self.failure = RuntimeError("private API token")
        response = self.client.post("/agent/investigate", json={"message": "Inspect demo."})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["answer"], "Agent investigation failed.")
        self.assertEqual(response.json()["status"], "failed")
        self.assertNotIn("private", response.text)

    def test_failure_retains_partial_history_and_tool_metadata(self):
        self.result = {"messages": [
            HumanMessage(content="Inspect demo."),
            AIMessage(content="", tool_calls=[{"name": "get_logs", "args": {}, "id": "logs-1"}]),
            ToolMessage(content="partial evidence", name="get_logs", tool_call_id="logs-1"),
        ]}
        for failure, code in ((RuntimeError("do-not-persist-this"), 500),
                              (GraphRecursionError("do-not-persist-this"), 503)):
            self.failure = failure
            response = self.client.post("/agent/investigate", json={"message": "Inspect demo."})
            self.assertEqual(response.status_code, code)
            body = response.json()
            InvestigateResponse.model_validate(body)
            self.assertEqual(body["metadata"]["tool_call_count"], 1)
            history = self.entries("messages")[-1]
            self.assertEqual(history["messages"][-1]["data"]["content"], "partial evidence")
            operations = self.entries("operations")[-3:]
            self.assertEqual([entry["event"] for entry in operations], [
                "investigation_started", "investigation_error", "investigation_completed",
            ])
            self.assertEqual(operations[1]["error_type"], type(failure).__name__)
            self.assertTrue(operations[1]["frames"])
            self.assertEqual(operations[-1]["status"], "failed")
            self.assertTrue(all(entry["investigation_id"] == body["investigation_id"] for entry in operations))
        self.assertNotIn("do-not-persist-this", (self.log_directory / "operations.jsonl").read_text())

    def test_failure_before_first_graph_state_still_logs_request(self):
        async def fail(*args, **kwargs):
            raise RuntimeError("do-not-persist-this")
            yield

        self.graph.astream.side_effect = fail
        response = self.client.post("/agent/investigate", json={"message": "Inspect demo."})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.entries("messages")[0]["messages"][0]["data"]["content"], "Inspect demo.")

    def test_cancellation_is_logged_and_propagates(self):
        self.failure = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(investigate(InvestigateRequest(message="Inspect demo."), Request({"type": "http", "app": app})))
        self.assertEqual(self.entries("operations")[-2]["error_type"], "CancelledError")
        self.assertEqual(self.entries("messages")[0]["status"], "failed")

    def test_log_write_failure_does_not_change_response_or_leak_payload(self):
        handler = app.state.messages_log.handlers[0]
        self.result = {"messages": [AIMessage(content="password=do-not-leak")]}
        with patch.object(handler, "_open", side_effect=OSError("private disk details")), \
             patch("sys.stderr", new_callable=StringIO) as stderr:
            handler.stream.close()
            handler.stream = None
            response = self.client.post("/agent/investigate", json={"message": "Inspect demo."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(stderr.getvalue())["event"], "investigation_log_write_failed")
        self.assertNotIn("do-not-leak", stderr.getvalue())
        self.assertNotIn("private disk details", stderr.getvalue())
        self.assertEqual(self.entries("operations")[-1]["status"], "completed")

    def test_endpoint_is_documented_as_post(self):
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(set(schema["paths"]["/agent/investigate"]), {"post"})
        self.assertEqual(self.client.get("/agent/investigate").status_code, 405)

    def test_unified_app_serves_health_evidence_and_investigations(self):
        report = {"pod": {"name": "demo", "namespace": "aiops"}, "collection_errors": []}
        with patch("api.collect_pod_evidence", return_value=report) as collector:
            self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
            collector.assert_not_called()
            self.graph.astream.assert_not_called()
            response = self.client.get("/api/v1/pods/aiops/demo/evidence", params={"tail_lines": 20})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), report)
            collector.assert_called_once_with("aiops", "demo", tail_lines=20)
            self.graph.astream.assert_not_called()
            response = self.client.post("/agent/investigate", json={"message": "Inspect demo in aiops."})
            self.assertEqual(response.status_code, 200)
            self.graph.astream.assert_called_once()
        self.factory.assert_called_once_with()

    def test_unified_openapi_includes_all_three_endpoints(self):
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(set(schema["paths"]), {
            "/health", "/api/v1/pods/{namespace}/{pod_name}/evidence", "/agent/investigate",
        })
        self.assertEqual(self.client.get("/docs").status_code, 200)

    def test_evidence_errors_preserve_existing_http_status(self):
        with patch("api.collect_pod_evidence", side_effect=PodEvidenceError("private token", 404)):
            response = self.client.get("/api/v1/pods/aiops/demo/evidence")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Pod not found."})
        self.graph.astream.assert_not_called()


class BackendGraphTests(PrivateLogTests):
    def test_graph_construction_waits_for_lifespan_startup(self):
        with patch("backend.main.load_dotenv"), patch("backend.main.create_graph") as factory:
            client = TestClient(app)
            factory.assert_not_called()
            with client:
                factory.assert_called_once_with()
                self.assertIs(app.state.agent, factory.return_value)

    def test_missing_model_key_fails_startup(self):
        with patch("backend.main.load_dotenv"), patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY"):
                with TestClient(app):
                    pass

    def test_http_request_uses_real_async_graph_and_tool_node(self):
        model = Mock()
        model.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{
                "name": "list_pods", "args": {"namespace": "aiops"}, "id": "list-1",
            }]),
            AIMessage(content="No Pods found in namespace aiops."),
        ]
        evidence = {"namespace": "aiops", "pods": [], "truncated": False, "collection_errors": []}
        with patch("backend.main.load_dotenv"), \
             patch("agent.graph.create_model", return_value=model), \
             patch("agent.tools.pod_listing.collect_pods", return_value=evidence) as collector:
            with TestClient(app) as client:
                response = client.post("/agent/investigate", json={"message": "List Pods in namespace aiops."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "No Pods found in namespace aiops.")
        self.assertEqual(response.json()["metadata"]["tools_used"], ["list_pods"])
        self.assertEqual(response.json()["metadata"]["tool_call_count"], 1)
        collector.assert_called_once_with("aiops")
        self.assertEqual(model.invoke.call_count, 2)
        messages = model.invoke.call_args.args[0]["messages"]
        self.assertIsInstance(messages[-1], ToolMessage)
        self.assertEqual(messages[-1].tool_call_id, "list-1")

    def test_real_graph_failure_after_tool_execution_retains_evidence(self):
        model = Mock()
        model.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{
                "name": "list_pods", "args": {"namespace": "aiops"}, "id": "list-1",
            }]),
            RuntimeError("private model failure"),
        ]
        evidence = {"namespace": "aiops", "pods": [], "truncated": False, "collection_errors": []}
        with patch("backend.main.load_dotenv"), \
             patch("agent.graph.create_model", return_value=model), \
             patch("agent.tools.pod_listing.collect_pods", return_value=evidence):
            with TestClient(app) as client:
                response = client.post("/agent/investigate", json={"message": "List Pods in namespace aiops."})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["metadata"]["tool_call_count"], 1)
        history = self.entries("messages")[0]["messages"]
        self.assertEqual([message["type"] for message in history], ["human", "ai", "tool"])
        self.assertEqual(json.loads(history[-1]["data"]["content"])["evidence"]["pods"], [])
        self.assertNotIn("private model failure", (self.log_directory / "operations.jsonl").read_text())


class LoggingTests(PrivateLogTests):
    def configure(self, **kwargs):
        loggers = configure_logging(self.log_directory, **kwargs)
        for logger in loggers:
            self.addCleanup(self.close_logger, logger)
        return loggers

    @staticmethod
    def close_logger(logger):
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def test_rotation_permissions_and_bounded_storage(self):
        operations, history = self.configure(max_bytes=1024, backup_count=2)
        for index in range(30):
            for logger in (operations, history):
                log_event(logger, "test_event", investigation_id=str(index), payload="diagnostic " * 25)
        files = list(self.log_directory.iterdir())
        self.assertEqual(len(files), 6)
        self.assertEqual(stat.S_IMODE(self.log_directory.stat().st_mode), 0o700)
        for path in files:
            self.assertLessEqual(path.stat().st_size, 1024)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            for line in path.read_text().splitlines():
                json.loads(line)
        self.assertEqual(self.entries("operations")[-1]["investigation_id"], "29")

    def test_oversized_history_is_reconstructable_and_files_remain_bounded(self):
        _, history = self.configure(max_bytes=2048, backup_count=10)
        answer = "## Diagnosis\n" + "Kubernetes \"failure\" ☁\\\n" * 100
        log_event(history, "investigation_messages", investigation_id="example-id",
                  messages=[{"type": "ai", "data": {"content": answer}}])
        fragments = []
        for path in self.log_directory.glob("messages.jsonl*"):
            self.assertLessEqual(path.stat().st_size, 2048)
            fragments.extend(json.loads(line) for line in path.read_text().splitlines())
        self.assertTrue(all(item["event"] == "investigation_log_fragment" for item in fragments))
        self.assertEqual(len(fragments), fragments[0]["parts"])
        restored = json.loads("".join(item["json_fragment"] for item in sorted(fragments, key=lambda item: item["part"])))
        self.assertEqual(restored["messages"][0]["data"]["content"], answer)

    def test_reconfiguration_does_not_duplicate_handlers_or_events(self):
        self.configure()
        operations, _ = self.configure()
        self.assertEqual(len(operations.handlers), 1)
        log_event(operations, "single_event")
        self.assertEqual(len(self.entries("operations")), 1)
        self.assertFalse(operations.propagate)

    def test_known_secrets_and_additional_literals_are_redacted(self):
        with patch.dict(os.environ, {
            "EXAMPLE_API_KEY": "opaque-environment-value",
            "AIOPS_LOG_REDACT_VALUES": "private-project-name\nprivate-account-number",
        }):
            operations, _ = self.configure()
        log_event(operations, "test_event", message="opaque-environment-value private-project-name private-account-number")
        saved = (self.log_directory / "operations.jsonl").read_text()
        for value in ("opaque-environment-value", "private-project-name", "private-account-number"):
            self.assertNotIn(value, saved)

    def test_redaction_handles_structured_and_free_text_secrets(self):
        original = {
            "nested": {"apiKey": "api-value", "credentials": {"value": "credential-value"}},
            "environment": [{"name": "DB_PASSWORD", "value": "env-value"}],
            "secret": {"kind": "Secret", "data": {"opaque": "base64-value"}},
            "message": 'Authorization: Bearer bearer-value password="two word secret" '
                       'https://db-user:db-pass@db.example.com/?token=query-secret '
                       'alice@example.com 10.20.30.40 '
                       '-----BEGIN PRIVATE KEY-----\nprivate-key-body\n-----END PRIVATE KEY-----',
            "json_content": json.dumps({"password": "json-value"}),
        }
        output = json.dumps(redact(original))
        for value in ("api-value", "credential-value", "env-value", "base64-value", "bearer-value",
                      "two word secret", "db-user", "db-pass", "query-secret", "alice@example.com",
                      "10.20.30.40", "private-key-body", "json-value"):
            self.assertNotIn(value, output)
        self.assertEqual(original["nested"]["apiKey"], "api-value")


class BackendEnvironmentTests(PrivateLogTests):
    def test_root_env_is_loaded_before_building_graph(self):
        with TemporaryDirectory(prefix="aiops-env-") as directory, patch.dict(os.environ):
            root = Path(directory)
            (root / ".env").write_text("GROQ_API_KEY=test-only-from-file\n")
            os.environ.pop("GROQ_API_KEY", None)
            with patch("backend.main.__file__", str(root / "backend" / "main.py")):
                with TestClient(app):
                    self.assertEqual(os.environ["GROQ_API_KEY"], "test-only-from-file")
                    self.assertTrue(callable(app.state.agent.ainvoke))

    def test_exported_key_takes_precedence_over_env_file(self):
        with TemporaryDirectory(prefix="aiops-env-") as directory, \
             patch.dict(os.environ, {"GROQ_API_KEY": "test-only-exported"}):
            root = Path(directory)
            (root / ".env").write_text("GROQ_API_KEY=test-only-from-file\n")
            with patch("backend.main.__file__", str(root / "backend" / "main.py")):
                with TestClient(app):
                    self.assertEqual(os.environ["GROQ_API_KEY"], "test-only-exported")


if __name__ == "__main__":
    unittest.main()
