import unittest
from unittest.mock import patch, MagicMock

from app.config import config
from app.models.exception import LLMGenerationError
from app.services import llm


class TestLLMFallbackChain(unittest.TestCase):
    def setUp(self):
        self.original_app_config = dict(config.app)
        config.app.clear()
        config.app.update({
            "llm_provider": "groq",
            "groq_api_key": "gsk_test_main",
            "groq_model_name": "llama-3.3-70b-versatile",
            "fallback_llm_provider": "openai",
            "fallback_llm_api_key": "sk_test_fb1",
            "fallback_llm_model_name": "gpt-4o-mini",
            "fallback2_llm_provider": "ollama",
            "fallback2_llm_model_name": "llama3",
        })

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)

    def test_main_llm_succeeds_without_fallback(self):
        with patch.object(llm, "_invoke_single_llm_provider", return_value="Main Response") as mock_invoke:
            res = llm._generate_response("Test prompt")
            self.assertEqual(res, "Main Response")
            self.assertEqual(mock_invoke.call_count, 1)
            call_kwargs = mock_invoke.call_args[1]
            self.assertEqual(call_kwargs["llm_provider"], "groq")

    def test_main_fails_then_fallback1_succeeds(self):
        def side_effect(llm_provider, **kwargs):
            if llm_provider == "groq":
                raise ValueError("429 Rate limit / Quota exceeded")
            return "Fallback 1 Response"

        with patch.object(llm, "_invoke_single_llm_provider", side_effect=side_effect) as mock_invoke:
            res = llm._generate_response("Test prompt")
            self.assertEqual(res, "Fallback 1 Response")
            self.assertEqual(mock_invoke.call_count, 2)
            first_provider = mock_invoke.call_args_list[0][1]["llm_provider"]
            second_provider = mock_invoke.call_args_list[1][1]["llm_provider"]
            self.assertEqual(first_provider, "groq")
            self.assertEqual(second_provider, "openai")

    def test_main_and_fallback1_fail_then_fallback2_succeeds(self):
        def side_effect(llm_provider, **kwargs):
            if llm_provider == "groq":
                raise ValueError("429 Quota Exceeded")
            elif llm_provider == "openai":
                raise ValueError("500 Internal Server Error")
            return "Fallback 2 Ollama Response"

        with patch.object(llm, "_invoke_single_llm_provider", side_effect=side_effect) as mock_invoke:
            res = llm._generate_response("Test prompt")
            self.assertEqual(res, "Fallback 2 Ollama Response")
            self.assertEqual(mock_invoke.call_count, 3)
            providers = [c[1]["llm_provider"] for c in mock_invoke.call_args_list]
            self.assertEqual(providers, ["groq", "openai", "ollama"])

    def test_all_providers_fail_raises_llm_generation_error(self):
        with patch.object(llm, "_invoke_single_llm_provider", side_effect=ValueError("Failed")):
            with self.assertRaises(LLMGenerationError) as ctx:
                llm._generate_response("Test prompt")
            err_msg = str(ctx.exception)
            self.assertIn("groq", err_msg)
            self.assertIn("openai", err_msg)
            self.assertIn("ollama", err_msg)

    def test_connection_with_explicit_provider_tests_only_that_provider(self):
        with patch.object(llm, "_invoke_single_llm_provider", return_value="OK") as mock_invoke:
            ok, err, elapsed = llm.test_connection(
                provider_id="deepseek",
                api_key="sk_ds_test",
                model_name="deepseek-chat"
            )
            self.assertTrue(ok)
            self.assertEqual(err, "")
            mock_invoke.assert_called_once_with(
                llm_provider="deepseek",
                api_key="sk_ds_test",
                model_name="deepseek-chat",
                base_url=None,
                prompt="Reply with exactly: OK"
            )


if __name__ == "__main__":
    unittest.main()
