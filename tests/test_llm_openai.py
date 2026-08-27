"""Contract của provider OpenAI (ChatGPT) + wiring trong adapters.factory.

Mock requests.post -- KHÔNG BAO GIỜ gọi mạng trong test. Đảm bảo cùng hợp
đồng với llm_gemini: rewrite() free-form, rewrite_json() JSON mode, thiếu
key raise RuntimeError, không log key.
"""
import os
import unittest
from unittest import mock

from acp.adapters import factory
from acp.core import llm_openai, openai_settings


def _fake_response(content, status=200):
    response = mock.Mock()
    response.status_code = status
    response.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return response


def _fake_responses_response(content, status=200):
    response = mock.Mock()
    response.status_code = status
    response.json.return_value = {
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": content}],
        }]
    }
    return response


class LlmOpenaiTests(unittest.TestCase):
    def setUp(self):
        for key in ("ACP_OPENAI_API_KEY", "ACP_OPENAI_MODEL",
                    "ACP_OPENAI_BASE_URL", "ACP_OPENAI_TEMPERATURE"):
            self._saved[key] = os.environ.pop(key, None)

    _saved = {}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_missing_key_raises_without_network(self):
        with mock.patch.object(llm_openai.requests, "post") as post:
            with self.assertRaises(RuntimeError):
                llm_openai.rewrite("prompt")
            post.assert_not_called()

    def test_rewrite_posts_chat_completions_with_key_and_model(self):
        os.environ["ACP_OPENAI_API_KEY"] = "sk-test-123"
        with mock.patch.object(llm_openai.requests, "post") as post:
            post.return_value = _fake_response("caption tự nhiên")
            out = llm_openai.rewrite("prompt")
        self.assertEqual(out, "caption tự nhiên")
        args, kwargs = post.call_args
        self.assertTrue(args[0].startswith("https://api.openai.com/v1/"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test-123")
        body = kwargs["json"]
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertNotIn("response_format", body)

    def test_rewrite_json_enforces_json_object_format(self):
        os.environ["ACP_OPENAI_API_KEY"] = "sk-test-123"
        with mock.patch.object(llm_openai.requests, "post") as post:
            post.return_value = _fake_response('{"hook": "..."}')
            out = llm_openai.rewrite_json("prompt")
        self.assertEqual(out, '{"hook": "..."}')
        self.assertEqual(
            post.call_args.kwargs["json"]["response_format"],
            {"type": "json_object"},
        )

    def test_gpt_56_uses_stateless_responses_api(self):
        with mock.patch.object(
            llm_openai, "_config",
            return_value=("sk-test", "gpt-5.6-luna", "https://api.openai.com/v1"),
        ), mock.patch.object(llm_openai.requests, "post") as post:
            post.return_value = _fake_responses_response("caption 5.6")
            out = llm_openai.rewrite("prompt")
        self.assertEqual(out, "caption 5.6")
        self.assertTrue(post.call_args.args[0].endswith("/responses"))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["input"], "prompt")
        self.assertFalse(body["store"])
        self.assertEqual(body["reasoning"], {"effort": "none"})
        self.assertNotIn("temperature", body)

    def test_gpt_56_json_uses_responses_text_format(self):
        with mock.patch.object(
            llm_openai, "_config",
            return_value=("sk-test", "gpt-5.6-terra", "https://api.openai.com/v1"),
        ), mock.patch.object(llm_openai.requests, "post") as post:
            post.return_value = _fake_responses_response('{"ok":true}')
            out = llm_openai.rewrite_json("prompt")
        self.assertEqual(out, '{"ok":true}')
        self.assertEqual(
            post.call_args.kwargs["json"]["text"]["format"],
            {"type": "json_object"},
        )

    def test_base_url_and_model_overridable_via_env(self):
        os.environ["ACP_OPENAI_API_KEY"] = "sk-test-123"
        os.environ["ACP_OPENAI_MODEL"] = "gpt-4.1-mini"
        os.environ["ACP_OPENAI_BASE_URL"] = "https://gateway.example/v1/"
        with mock.patch.object(llm_openai.requests, "post") as post:
            post.return_value = _fake_response("ok")
            llm_openai.rewrite("p")
        self.assertTrue(post.call_args.args[0].startswith("https://gateway.example/v1/chat/completions"))
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gpt-4.1-mini")

    def test_http_error_does_not_leak_response_body(self):
        os.environ["ACP_OPENAI_API_KEY"] = "sk-test-123"
        with mock.patch.object(llm_openai.requests, "post") as post:
            post.return_value = _fake_response("", status=401)
            with self.assertRaises(RuntimeError) as ctx:
                llm_openai.rewrite("p")
        message = str(ctx.exception)
        self.assertIn("401", message)
        self.assertNotIn("sk-test-123", message)

    def test_http_error_includes_safe_openai_code(self):
        os.environ["ACP_OPENAI_API_KEY"] = "sk-test-123"
        response = _fake_response("", status=429)
        response.json.return_value = {
            "error": {"code": "insufficient_quota", "message": "secret detail"}
        }
        with mock.patch.object(llm_openai.requests, "post", return_value=response):
            with self.assertRaises(RuntimeError) as ctx:
                llm_openai.rewrite("p")
        self.assertIn("insufficient_quota", str(ctx.exception))
        self.assertNotIn("secret detail", str(ctx.exception))


class FactoryOpenaiWiringTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in
                      ("ACP_CAPTION_LLM", "ACP_CONTENT_ENGINE_LLM")}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_caption_llm_routes_to_openai_rewrite(self):
        os.environ["ACP_CAPTION_LLM"] = "openai"
        self.assertEqual(factory.get_caption_llm().__name__, "rewrite")

    def test_chatgpt_alias_accepted(self):
        os.environ["ACP_CAPTION_LLM"] = "chatgpt"
        self.assertIsNotNone(factory.get_caption_llm())
        self.assertIsNotNone(factory.get_seeding_llm())

    def test_content_engine_llm_routes_to_openai_json(self):
        os.environ["ACP_CONTENT_ENGINE_LLM"] = "openai"
        callback = factory.get_content_engine_llm()
        self.assertEqual(callback.__name__, "rewrite_json")

    def test_unknown_choice_disables_llm(self):
        os.environ["ACP_CAPTION_LLM"] = "chatglm"
        self.assertIsNone(factory.get_caption_llm())

    def test_ui_model_catalog_contains_all_gpt_56_tiers(self):
        self.assertTrue({
            "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
        }.issubset(openai_settings.MODEL_IDS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
