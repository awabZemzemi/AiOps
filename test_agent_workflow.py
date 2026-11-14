"""Offline regression tests: python -m unittest test_agent_workflow -v."""

from contextlib import ExitStack
import json
import os
import unittest
from unittest.mock import Mock, patch

from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agent.graph import create_graph
from agent.factory import create_model
from agent.state import AgentState
from agent.tools import get_tools
from evidence_common import PodEvidenceError
from models import MAX_TEXT_CHARS


def tool_request(name, args, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tools = get_tools()
        self.tool_graph = (
            StateGraph(AgentState)
            .add_node("tools", ToolNode(self.tools))
            .add_edge(START, "tools")
            .add_edge("tools", END)
            .compile()
        )
        self.target = {"namespace": "aiops", "pod_name": "demo"}
        self.evidence = {
            "get_pod_info": {"pod": {"name": "demo", "namespace": "aiops"},
                             "containers": [{"name": "app"}], "collection_errors": []},
            "list_pods": {"namespace": "aiops", "pods": [{"name": "demo"}],
                          "truncated": False, "collection_errors": []},
            "get_logs": {"logs": {"app": {"current": "", "previous": None}},
                         "collection_errors": []},
            "get_events": {"events": [], "collection_errors": []},
            "get_metrics": {"metrics": {"timestamp": "now", "window": "30s", "containers": [
                {"name": "app", "cpu": "5m", "memory": "20Mi"},
            ]}, "collection_errors": []},
        }
        collectors = {
            "get_pod_info": "agent.tools.pod_info.collect_pod_information",
            "list_pods": "agent.tools.pod_listing.collect_pods",
            "get_logs": "agent.tools.logs.collect_container_logs",
            "get_events": "agent.tools.events.collect_kubernetes_events",
            "get_metrics": "agent.tools.metrics.collect_resource_metrics",
        }
        stack = self.enterContext(ExitStack())
        self.collectors = {
            name: stack.enter_context(patch(path, return_value=self.evidence[name]))
            for name, path in collectors.items()
        }
        self.args = {
            name: {"namespace": "aiops"} if name == "list_pods" else dict(self.target)
            for name in collectors
        }
        self.args["get_logs"]["container_name"] = "app"

    def invoke_tool(self, name, args=None):
        result = self.tool_graph.invoke({"messages": [tool_request(name, self.args[name] if args is None else args)]})
        self.assertEqual(set(result), {"messages"})
        message = result["messages"][-1]
        self.assertIsInstance(message, ToolMessage)
        self.assertEqual(message.tool_call_id, "call-1")
        return message, json.loads(message.content)

    def test_tools_expose_explicit_targets_and_hide_runtime(self):
        self.assertEqual({tool.name for tool in self.tools}, set(self.args))
        for tool in self.tools:
            with self.subTest(tool=tool.name):
                schema = tool.tool_call_schema.model_json_schema()
                self.assertEqual(set(schema["required"]), set(self.args[tool.name]))
                self.assertNotIn("runtime", schema["properties"])

    def test_graph_finishes_without_tools(self):
        with patch("agent.graph.create_model") as factory:
            factory.return_value.invoke.return_value = AIMessage(content="Please provide a namespace.")
            graph = create_graph()
            result = graph.invoke({"messages": [HumanMessage(content="Check my Pods.")]})
        self.assertEqual(set(result), {"messages"})
        self.assertEqual(len(result["messages"]), 2)
        self.assertEqual(result["messages"][-1].content, "Please provide a namespace.")
        self.assertEqual(factory.call_args.kwargs["tools"], self.tools)
        for collector in self.collectors.values():
            collector.assert_not_called()

    def test_full_graph_executes_all_five_tools_and_keeps_history(self):
        diagnostics = [
            {"name": name, "args": self.args[name], "id": name}
            for name in ("get_logs", "get_events", "get_metrics")
        ]
        model = Mock()
        model.invoke.side_effect = [
            tool_request("list_pods", self.args["list_pods"], "list"),
            tool_request("get_pod_info", self.target, "info"),
            AIMessage(content="", tool_calls=diagnostics),
            AIMessage(content="Diagnostics complete."),
        ]
        with patch("agent.graph.create_model", return_value=model):
            graph = create_graph()
            initial = {"messages": [HumanMessage(content="Inspect demo in namespace aiops.")]}
            result = graph.invoke(initial)
        self.assertEqual(set(result), {"messages"})
        self.assertEqual(len(result["messages"]), 10)
        self.assertEqual(len(initial["messages"]), 1)
        self.assertEqual(result["messages"][-1].content, "Diagnostics complete.")
        observations = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual({m.tool_call_id for m in observations}, {"list", "info", *[c["id"] for c in diagnostics]})
        self.assertTrue(all(m.status == "success" for m in observations))
        self.assertEqual(model.invoke.call_args.args[0]["messages"][-3:], observations[-3:])
        self.collectors["list_pods"].assert_called_once_with("aiops")
        self.collectors["get_pod_info"].assert_called_once_with("aiops", "demo")
        self.collectors["get_logs"].assert_called_once_with("aiops", "demo", "app", tail_lines=100)
        self.collectors["get_events"].assert_called_once_with("aiops", "demo")
        self.collectors["get_metrics"].assert_called_once_with("aiops", "demo")

    def test_parallel_calls_keep_each_namespace_and_pod(self):
        targets = [self.target, {"namespace": "other", "pod_name": "second"}]
        request = AIMessage(content="", tool_calls=[
            {"name": "get_metrics", "args": target, "id": str(i)}
            for i, target in enumerate(targets)
        ])
        messages = self.tool_graph.invoke({"messages": [request]})["messages"][1:]
        for message, target in zip(messages, targets):
            observation = json.loads(message.content)
            self.assertEqual({key: observation[key] for key in target}, target)
            self.collectors["get_metrics"].assert_any_call(target["namespace"], target["pod_name"])

    def test_recursion_limit_stops_repeated_tools_and_can_be_overridden(self):
        for limit in (None, 2, 4):
            with self.subTest(limit=limit), patch("agent.graph.create_model") as factory:
                factory.return_value.invoke.side_effect = lambda state: tool_request("list_pods", self.args["list_pods"])
                graph = create_graph()
                config = {} if limit is None else {"recursion_limit": limit}
                with self.assertRaises(GraphRecursionError):
                    graph.invoke({"messages": [HumanMessage(content="Inspect namespace aiops.")]}, config=config)
                self.assertEqual(factory.return_value.invoke.call_count, (limit or 16) // 2)

    def test_stream_preserves_message_only_state(self):
        with patch("agent.graph.create_model") as factory:
            factory.return_value.invoke.side_effect = [
                tool_request("list_pods", self.args["list_pods"]), AIMessage(content="Done."),
            ]
            values = list(create_graph().stream({"messages": [("user", "List Pods in aiops.")]}, stream_mode="values"))
        self.assertTrue(all(set(value) == {"messages"} for value in values))
        self.assertEqual(len(values[-1]["messages"]), 4)

    def test_collector_failures_are_safe_tool_messages(self):
        for name, collector in self.collectors.items():
            for error, status in [
                (PodEvidenceError("private token", 404), "not_found"),
                (ApiException(status=403, reason="private token"), "permission_denied"),
                (OSError("private token"), "connection_error"),
                (ValueError("private token"), "invalid_input"),
            ]:
                with self.subTest(tool=name, error=status):
                    collector.side_effect = error
                    message, observation = self.invoke_tool(name)
                    self.assertEqual(message.status, "error")
                    self.assertEqual(observation["status"], status)
                    self.assertNotIn("private token", message.content)

    def test_kubeconfig_error_is_reported_without_details(self):
        error = PodEvidenceError("private config")
        error.__cause__ = ConfigException("private config")
        self.collectors["get_pod_info"].side_effect = error
        message, observation = self.invoke_tool("get_pod_info")
        self.assertEqual(observation["status"], "configuration_error")
        self.assertNotIn("private config", message.content)

    def test_pod_identity_mismatch_is_not_verified(self):
        self.evidence["get_pod_info"]["pod"]["name"] = "wrong"
        message, observation = self.invoke_tool("get_pod_info")
        self.assertEqual(message.status, "error")
        self.assertEqual(observation["status"], "invalid_evidence")

    def test_empty_and_partial_evidence_is_preserved(self):
        for name in ("get_logs", "get_events", "list_pods"):
            with self.subTest(tool=name):
                message, observation = self.invoke_tool(name)
                self.assertEqual(message.status, "success")
                self.assertEqual(observation["evidence"], self.evidence[name])
        errors = [{"operation": "logs.previous", "status": 400}]
        self.evidence["get_logs"]["collection_errors"] = errors
        message, observation = self.invoke_tool("get_logs")
        self.assertEqual(message.status, "success")
        self.assertEqual(observation["status"], "partial")
        self.assertEqual(observation["evidence"]["logs"]["app"]["current"], "")

    def test_unavailable_metrics_and_truncated_listing(self):
        self.evidence["get_metrics"].update(metrics=None, collection_errors=[{"status": 404}])
        message, observation = self.invoke_tool("get_metrics")
        self.assertEqual(message.status, "error")
        self.assertEqual(observation["status"], "collection_error")
        self.evidence["list_pods"]["truncated"] = True
        message, observation = self.invoke_tool("list_pods")
        self.assertEqual(message.status, "success")
        self.assertEqual(observation["status"], "partial")

    def test_metrics_observations_remain_bounded(self):
        containers = [{"name": "app", "cpu": "x" * (MAX_TEXT_CHARS + 1), "memory": "1Mi"}] * 101
        self.evidence["get_metrics"]["metrics"]["containers"] = containers
        _, observation = self.invoke_tool("get_metrics")
        bounded = observation["evidence"]
        self.assertEqual(observation["status"], "partial")
        self.assertEqual(len(bounded["metrics"]["containers"]), 100)
        self.assertEqual(len(bounded["metrics"]["containers"][0]["cpu"]), MAX_TEXT_CHARS)
        self.assertEqual(len(containers), 101)
        self.assertEqual(len(containers[0]["cpu"]), MAX_TEXT_CHARS + 1)

    def test_missing_tool_arguments_and_invalid_log_limit_do_not_collect(self):
        for name in self.args:
            with self.subTest(tool=name):
                message = self.tool_graph.invoke({"messages": [tool_request(name, {})]})["messages"][-1]
                self.assertEqual(message.status, "error")
                self.collectors[name].assert_not_called()
        for limit in (0, 1001, True):
            message = self.tool_graph.invoke({"messages": [tool_request(
                "get_logs", {**self.args["get_logs"], "tail_lines": limit},
            )]})["messages"][-1]
            self.assertEqual(message.status, "error")
        self.collectors["get_logs"].assert_not_called()

    def test_model_binds_all_tools_with_message_only_prompt(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only-no-network"}):
            pipeline = create_model(tools=self.tools)
        self.assertEqual(pipeline.first.input_variables, ["messages"])
        self.assertEqual({d["function"]["name"] for d in pipeline.last.kwargs["tools"]}, set(self.args))

    def test_model_configuration_errors_are_clear(self):
        with self.assertRaisesRegex(ValueError, "Unknown LLM profile"):
            create_model("missing")
        with patch.dict(os.environ, {"GROQ_API_KEY": " "}):
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY"):
                create_model()


if __name__ == "__main__":
    unittest.main()
