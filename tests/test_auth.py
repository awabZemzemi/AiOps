"""Offline security regression suite; real SQL/JWT/Argon2, no Groq or Kubernetes."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
import jwt
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from sqlalchemy import inspect, select

from agent.authorization import AuthorizationContext, NamespaceAccessDenied
from agent.state import AgentState
from agent.tools import get_tools
from tests.auth_support import prepare_auth, TEST_PASSWORD, test_password_hash
from backend.auth import bootstrap
from backend.auth.config import AuthSettings
from backend.auth.database import make_engine
from backend.auth.models import User
from backend.auth.rate_limit import LoginLimiter
from backend.auth.security import create_token, verify_password
from backend.main import app


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.sessions, self.admin, self.headers = prepare_auth(self, ["aiops"])
        self.logs = Path(self.enterContext(TemporaryDirectory(prefix="auth-logs-")))
        self.enterContext(patch.dict(os.environ, {"AIOPS_LOG_DIR": str(self.logs)}))
        self.enterContext(patch("backend.main.load_dotenv"))
        self.graph = Mock()

        async def stream(state, **kwargs):
            yield {"messages": [*state["messages"], AIMessage(content="Done.")]}

        self.graph.astream.side_effect = stream
        self.enterContext(patch("backend.main.create_graph", return_value=self.graph))
        self.client = self.enterContext(TestClient(app))

    def seed_operator(self, username="operator", namespaces=None):
        with self.sessions.begin() as session:
            user = User(username=username, password_hash=test_password_hash(), role="operator",
                        allowed_namespaces=namespaces if namespaces is not None else ["aiops"])
            session.add(user)
            session.flush()
        return user, {"Authorization": f"Bearer {create_token(user.id, AuthSettings.from_env())}"}

    def create_operator(self, **changes):
        return self.client.post("/users", headers=self.headers, json={
            "username": "operator", "password": TEST_PASSWORD,
            "role": "operator", "allowed_namespaces": ["aiops"], **changes,
        })

    def test_successful_login_and_me_use_immutable_subject(self):
        response = self.client.post("/auth/token", data={"username": "admin", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertEqual(body["expires_in"], 900)
        claims = jwt.decode(body["access_token"], AuthSettings.from_env().secret, algorithms=["HS256"],
                            issuer="aiops-backend", audience="aiops-api")
        self.assertEqual(claims["sub"], str(self.admin.id))
        self.assertNotIn("role", claims)
        me = self.client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["id"], str(self.admin.id))
        self.assertNotIn("password", me.text)

    def test_unknown_wrong_and_disabled_login_errors_are_identical(self):
        attempts = [("unknown", TEST_PASSWORD), ("admin", "wrong")]
        user, _ = self.seed_operator()
        with self.sessions.begin() as session:
            session.get(User, user.id).is_active = False
        attempts.append(("operator", TEST_PASSWORD))
        bodies = []
        for username, password in attempts:
            response = self.client.post("/auth/token", data={"username": username, "password": password})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["www-authenticate"], "Bearer")
            bodies.append(response.json())
        self.assertTrue(all(body == bodies[0] for body in bodies))

    def test_missing_authentication_on_all_protected_routes(self):
        for method, url in [("get", "/auth/me"), ("get", "/users"), ("post", "/users"),
                            ("patch", f"/users/{uuid4()}"), ("post", "/agent/investigate"),
                            ("get", "/api/v1/pods/aiops/demo/evidence")]:
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 401, (url, response.text))
            self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.graph.astream.assert_not_called()

    def test_invalid_jwt_claims_signatures_and_algorithms(self):
        settings = AuthSettings.from_env()
        now = datetime.now(timezone.utc)
        claims = {"sub": str(self.admin.id), "iat": now, "nbf": now,
                  "exp": now + timedelta(minutes=15), "iss": settings.issuer, "aud": settings.audience}
        variants = [
            (dict(claims, exp=now - timedelta(seconds=1)), settings.secret, "HS256"),
            (dict(claims, iss="wrong"), settings.secret, "HS256"),
            (dict(claims, aud="wrong"), settings.secret, "HS256"),
            (dict(claims, sub="admin"), settings.secret, "HS256"),
            (dict(claims, sub=str(uuid4())), settings.secret, "HS256"),
            (dict(claims, nbf=now + timedelta(hours=1)), settings.secret, "HS256"),
            (claims, "different-test-only-signing-key-123456789", "HS256"),
            (claims, settings.secret, "HS384"),
            (claims, "", "none"),
        ]
        variants.extend(({k: v for k, v in claims.items() if k != field}, settings.secret, "HS256")
                        for field in claims)
        tokens = [jwt.encode(c, key, algorithm=algorithm) for c, key, algorithm in variants]
        tokens.extend(["garbage", "a.b.c"])
        for token in tokens:
            with self.subTest(token_type=token[:5]):
                response = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 401)

    def test_admin_creates_operator_with_only_a_salted_argon2id_hash_persisted(self):
        response = self.create_operator()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["role"], "operator")
        with self.sessions() as session:
            user = session.scalar(select(User).where(User.username == "operator"))
            self.assertTrue(user.password_hash.startswith("$argon2id$"))
            self.assertTrue(verify_password(user.password_hash, TEST_PASSWORD))
            self.assertNotEqual(user.password_hash, self.admin.password_hash)
        self.assertNotIn("password", response.text)
        self.assertEqual(self.create_operator().status_code, 409)
        self.assertEqual(len(self.client.get("/users", headers=self.headers).json()), 2)

    def test_operator_cannot_create_list_modify_or_self_escalate(self):
        user, headers = self.seed_operator()
        for method, url, kwargs in [
            ("post", "/users", {"json": {"username": "another", "password": TEST_PASSWORD}}),
            ("get", "/users", {}),
            ("patch", f"/users/{user.id}", {"json": {"role": "admin"}}),
            ("patch", f"/users/{self.admin.id}", {"json": {"is_active": False}}),
        ]:
            response = getattr(self.client, method)(url, headers=headers, **kwargs)
            self.assertEqual(response.status_code, 403)
        with self.sessions() as session:
            self.assertEqual(session.get(User, user.id).role, "operator")

    def test_admin_password_can_be_short_but_must_be_nonempty(self):
        response = self.create_operator(username="short-admin", role="admin", password="x")
        self.assertEqual(response.status_code, 201)
        login = self.client.post("/auth/token", data={"username": "short-admin", "password": "x"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.create_operator(username="empty-admin", role="admin", password="").status_code, 422)
        self.assertEqual(self.create_operator(username="short-operator", password="x").status_code, 422)

    def test_roles_namespaces_and_mutable_fields_are_validated_without_echoing_secrets(self):
        for changes in [{"role": "cluster-admin"}, {"allowed_namespaces": ["*"]},
                        {"allowed_namespaces": ["kube.system"]}, {"allowed_namespaces": [""]},
                        {"allowed_namespaces": ["a" * 64]}, {"allowed_namespaces": ["UPPER"]},
                        {"username": "Bad User"}, {"password": "tiny-secret"},
                        {"id": str(uuid4())}, {"password_hash": "do-not-echo"}]:
            response = self.create_operator(**changes)
            self.assertEqual(response.status_code, 422, changes)
            self.assertNotIn("tiny-secret", response.text)
            self.assertNotIn("do-not-echo", response.text)
        for changes in [{"role": None}, {"allowed_namespaces": None}, {"is_active": None},
                        {"username": "new-name"}, {"id": str(uuid4())}, {"password": TEST_PASSWORD}]:
            response = self.client.patch(f"/users/{self.admin.id}", headers=self.headers, json=changes)
            self.assertEqual(response.status_code, 422)
        response = self.client.post("/auth/token", data={"username": "admin", "password": "s" * 1025})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("s" * 1025, response.text)

    def test_final_active_admin_cannot_be_disabled_or_demoted(self):
        for changes in [{"role": "operator"}, {"is_active": False}, {"role": "operator", "is_active": False}]:
            response = self.client.patch(f"/users/{self.admin.id}", headers=self.headers, json=changes)
            self.assertEqual(response.status_code, 409)
        self.create_operator(username="second-admin", role="admin", is_active=False)
        response = self.client.patch(f"/users/{self.admin.id}", headers=self.headers, json={"is_active": False})
        self.assertEqual(response.status_code, 409)

    def test_admin_updates_permissions_without_changing_identity_and_records_utc_time(self):
        created = self.create_operator().json()
        response = self.client.patch(f"/users/{created['id']}", headers=self.headers,
                                     json={"role": "admin", "allowed_namespaces": ["other", "other"]})
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(updated["created_at"], created["created_at"])
        self.assertEqual(updated["allowed_namespaces"], ["other"])
        self.assertEqual(updated["role"], "admin")
        self.assertGreater(datetime.fromisoformat(updated["updated_at"]), datetime.fromisoformat(created["updated_at"]))
        self.assertTrue(updated["updated_at"].endswith("Z"))
        self.assertEqual(self.client.patch(f"/users/{uuid4()}", headers=self.headers, json={"is_active": False}).status_code, 404)

    def test_structured_namespace_narrows_tool_permissions(self):
        from agent.graph import create_graph
        with self.sessions.begin() as session:
            session.get(User, self.admin.id).allowed_namespaces = ["aiops", "other"]
        model = Mock()
        model.invoke.return_value = AIMessage(content="", tool_calls=[{
            "name": "list_pods", "args": {"namespace": "other"}, "id": "wrong-scope",
        }])
        with patch("agent.graph.create_model", return_value=model), \
             patch("agent.tools.pod_listing.collect_pods") as collector:
            app.state.agent = create_graph()
            response = self.client.post("/agent/investigate", headers=self.headers,
                                        json={"namespace": "aiops", "message": "inspect"})
        self.assertEqual(response.status_code, 403)
        collector.assert_not_called()

    def test_concurrent_admin_demotions_leave_one_active_admin(self):
        response = self.create_operator(username="second-admin", role="admin")
        second_id = response.json()["id"]
        barrier = Barrier(2)

        def demote(user_id):
            barrier.wait(timeout=5)
            return self.client.patch(f"/users/{user_id}", headers=self.headers, json={"role": "operator"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(demote, [str(self.admin.id), second_id]))
        self.assertIn(200, codes)
        self.assertTrue(all(code in (200, 403, 409) for code in codes), codes)
        with self.sessions() as session:
            self.assertEqual(len(list(session.scalars(select(User).where(User.role == "admin", User.is_active.is_(True))))), 1)

    def test_permissions_status_and_role_are_reloaded_for_existing_tokens(self):
        user, headers = self.seed_operator()
        response = self.client.post("/agent/investigate", headers=headers,
                                    json={"message": "inspect", "namespace": "aiops"})
        self.assertEqual(response.status_code, 200)
        self.client.patch(f"/users/{user.id}", headers=self.headers, json={"allowed_namespaces": ["other"]})
        response = self.client.post("/agent/investigate", headers=headers,
                                    json={"message": "inspect", "namespace": "aiops"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/auth/me", headers=headers).json()["allowed_namespaces"], ["other"])
        self.client.patch(f"/users/{user.id}", headers=self.headers, json={"is_active": False})
        for url in ["/auth/me", "/api/v1/pods/other/demo/evidence"]:
            self.assertEqual(self.client.get(url, headers=headers).status_code, 401)
        self.assertEqual(self.client.post("/agent/investigate", headers=headers, json={"message": "inspect"}).status_code, 401)
        second = self.create_operator(username="second-admin", role="admin").json()
        second_headers = {"Authorization": f"Bearer {create_token(second['id'], AuthSettings.from_env())}"}
        self.client.patch(f"/users/{second['id']}", headers=self.headers, json={"role": "operator"})
        self.assertEqual(self.client.get("/users", headers=second_headers).status_code, 403)

    def test_namespace_permissions_apply_to_both_roles_and_direct_evidence(self):
        _, operator_headers = self.seed_operator()
        with patch("api.collect_pod_evidence", return_value={"collection_errors": []}) as collector:
            for headers in [self.headers, operator_headers]:
                self.assertEqual(self.client.get("/api/v1/pods/aiops/demo/evidence", headers=headers).status_code, 200)
                self.assertEqual(self.client.get("/api/v1/pods/forbidden/demo/evidence", headers=headers).status_code, 403)
            self.assertEqual(collector.call_count, 2)
        self.assertEqual(self.client.post("/agent/investigate", headers=self.headers,
                                         json={"message": "inspect", "namespace": "forbidden"}).status_code, 403)
        self.graph.astream.assert_not_called()

    def test_login_limits_include_unknown_users_and_ignore_forwarded_header_spoofing(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/auth/token", data={"username": "missing", "password": "x"}).status_code, 401)
        response = self.client.post("/auth/token", headers={"X-Forwarded-For": "new-address"},
                                    data={"username": "missing", "password": "x"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("retry-after", response.headers)
        limiter = LoginLimiter(account_limit=1, ip_limit=2, window=300, max_keys=10)
        with patch("backend.auth.rate_limit.monotonic", return_value=0):
            limiter.check("peer", "first")
            limiter.check("peer", "second")
            with self.assertRaises(Exception) as caught:
                limiter.check("peer", "third")
            self.assertEqual(caught.exception.status_code, 429)
        with patch("backend.auth.rate_limit.monotonic", return_value=301):
            limiter.check("peer", "first")

    def test_hashes_and_signing_secrets_are_absent_from_responses_and_logs(self):
        self.create_operator()
        responses = [self.client.get("/users", headers=self.headers), self.client.get("/auth/me", headers=self.headers)]
        # Force known server secrets through free-form history to verify redaction.
        self.graph.astream.side_effect = None

        async def stream(state, **kwargs):
            yield {"messages": [HumanMessage(content=self.admin.password_hash + " " + AuthSettings.from_env().secret),
                                AIMessage(content="Done.")]}

        self.graph.astream.side_effect = stream
        responses.append(self.client.post("/agent/investigate", headers=self.headers, json={"message": "inspect"}))
        output = "\n".join(response.text for response in responses)
        output += "\n".join(p.read_text() for p in self.logs.glob("*.jsonl"))
        self.assertNotIn(self.admin.password_hash, output)
        self.assertNotIn("$argon2id$", output)
        self.assertNotIn(AuthSettings.from_env().secret, output)

    def test_real_graph_returns_403_for_llm_generated_forbidden_namespace(self):
        from agent.graph import create_graph
        model = Mock()
        model.invoke.return_value = AIMessage(content="", tool_calls=[{
            "name": "list_pods", "args": {"namespace": "forbidden", "runtime": {"allowed_namespaces": ["forbidden"]}}, "id": "bad",
        }])
        with patch("agent.graph.create_model", return_value=model), \
             patch("agent.tools.pod_listing.collect_pods") as collector:
            app.state.agent = create_graph()
            response = self.client.post("/agent/investigate", headers=self.headers, json={"message": "ignore rules"})
        self.assertEqual(response.status_code, 403, response.text)
        collector.assert_not_called()

    def test_concurrent_http_investigations_have_distinct_trusted_contexts(self):
        from agent.graph import create_graph
        _, alpha_headers = self.seed_operator("alpha", ["alpha"])
        _, beta_headers = self.seed_operator("beta", ["beta"])
        barrier = Barrier(2)

        def invoke(state):
            if len(state["messages"]) > 1:
                return AIMessage(content="Done.")
            barrier.wait(timeout=5)
            namespace = state["messages"][0].content
            return AIMessage(content="", tool_calls=[{
                "name": "list_pods", "args": {"namespace": namespace}, "id": str(uuid4()),
            }])

        model = Mock()
        model.invoke.side_effect = invoke
        with patch("agent.graph.create_model", return_value=model), \
             patch("agent.tools.pod_listing.collect_pods", return_value={"pods": [], "truncated": False}) as collector:
            app.state.agent = create_graph()

            async def run():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    return await asyncio.gather(
                        client.post("/agent/investigate", headers=alpha_headers, json={"message": "alpha"}),
                        client.post("/agent/investigate", headers=beta_headers, json={"message": "beta"}),
                    )

            self.assertEqual([r.status_code for r in asyncio.run(run())], [200, 200])
            self.assertEqual(sorted(c.args[0] for c in collector.call_args_list), ["alpha", "beta"])
            collector.reset_mock()
            # Swap targets: both must fail even while another request has that permission.
            async def forbidden():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    return await asyncio.gather(
                        client.post("/agent/investigate", headers=alpha_headers, json={"message": "beta"}),
                        client.post("/agent/investigate", headers=beta_headers, json={"message": "alpha"}),
                    )
            self.assertEqual([r.status_code for r in asyncio.run(forbidden())], [403, 403])
            collector.assert_not_called()


class ToolAuthorizationTests(unittest.TestCase):
    def test_all_five_tools_fail_closed_before_collection_and_hide_context(self):
        graph = (StateGraph(AgentState, context_schema=AuthorizationContext)
                 .add_node("tools", ToolNode(get_tools())).add_edge(START, "tools").add_edge("tools", END).compile())
        collectors = ["pod_info.collect_pod_information", "logs.collect_container_logs",
                      "events.collect_kubernetes_events", "metrics.collect_resource_metrics", "pod_listing.collect_pods"]
        mocks = [self.enterContext(patch("agent.tools." + name)) for name in collectors]
        for tool in get_tools():
            schema = tool.tool_call_schema.model_json_schema()
            self.assertNotIn("runtime", schema["properties"])
            self.assertNotIn("allowed_namespaces", schema["properties"])
            args = {"namespace": "forbidden", "pod_name": "demo", "container_name": "app"}
            args = {key: value for key, value in args.items() if key in schema["properties"]}
            message = AIMessage(content="", tool_calls=[{"name": tool.name, "args": args, "id": "bad"}])
            for context in [None, AuthorizationContext(uuid4(), frozenset(["aiops"]))]:
                with self.subTest(tool=tool.name, context=context), self.assertRaises(NamespaceAccessDenied):
                    graph.invoke({"messages": [message]}, context=context)
        for collector in mocks:
            collector.assert_not_called()


class MigrationBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory(prefix="auth-migrations-")))
        self.url = f"sqlite:///{self.directory / 'users.db'}"
        self.enterContext(patch.dict(os.environ, {"AIOPS_DATABASE_URL": self.url}))
        self.config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))

    def test_migration_upgrade_downgrade_and_metadata_match(self):
        command.upgrade(self.config, "head")
        engine = make_engine(self.url)
        try:
            self.assertIn("users", inspect(engine).get_table_names())
            command.check(self.config)
            command.downgrade(self.config, "base")
            self.assertNotIn("users", inspect(engine).get_table_names())
            command.upgrade(self.config, "head")
            self.assertEqual(inspect(engine).get_columns("users")[0]["name"], "id")
        finally:
            engine.dispose()

    def test_hidden_prompt_bootstraps_once_and_never_prints_credentials(self):
        command.upgrade(self.config, "head")
        with patch("backend.auth.bootstrap.getpass", side_effect=[TEST_PASSWORD, TEST_PASSWORD]) as prompt, \
             redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()) as errors:
            self.assertEqual(bootstrap.main(["--username", "first-admin", "--namespace", "aiops"]), 0)
            self.assertEqual(bootstrap.main(["--username", "another-admin"]), 1)
        self.assertEqual(prompt.call_count, 2)
        self.assertNotIn(TEST_PASSWORD, output.getvalue() + errors.getvalue())
        self.assertNotIn("$argon2", output.getvalue() + errors.getvalue())
        self.assertEqual((self.directory / "users.db").stat().st_mode & 0o777, 0o600)

    def test_bootstrap_invalid_and_mismatched_passwords_are_safe(self):
        command.upgrade(self.config, "head")
        for passwords in [["", ""], [TEST_PASSWORD, "mismatch"]]:
            with patch("backend.auth.bootstrap.getpass", side_effect=passwords), redirect_stderr(StringIO()) as errors:
                self.assertEqual(bootstrap.main(["--username", "first-admin"]), 1)
                self.assertNotIn(TEST_PASSWORD, errors.getvalue())

    def test_bootstrap_accepts_admin_password_shorter_than_twelve(self):
        command.upgrade(self.config, "head")
        with patch("backend.auth.bootstrap.getpass", side_effect=["x", "x"]), redirect_stdout(StringIO()):
            self.assertEqual(bootstrap.main(["--username", "first-admin"]), 0)

    def test_bootstrap_refuses_a_password_prompt_that_cannot_hide_input(self):
        from getpass import GetPassWarning
        command.upgrade(self.config, "head")
        with patch("backend.auth.bootstrap.getpass", side_effect=GetPassWarning("cannot hide input")), \
             redirect_stderr(StringIO()) as errors:
            self.assertEqual(bootstrap.main(["--username", "first-admin"]), 1)
        self.assertIn("hidden terminal", errors.getvalue())

    def test_startup_requires_secret_and_migrations(self):
        with patch("backend.main.load_dotenv"), patch.dict(os.environ, {"AIOPS_JWT_SECRET": ""}):
            with self.assertRaisesRegex(ValueError, "AIOPS_JWT_SECRET"):
                with TestClient(app):
                    pass
        with patch("backend.main.load_dotenv"), patch.dict(os.environ, {"AIOPS_JWT_SECRET": "x" * 48}):
            with self.assertRaisesRegex(RuntimeError, "alembic upgrade head"):
                with TestClient(app):
                    pass


if __name__ == "__main__":
    unittest.main()
