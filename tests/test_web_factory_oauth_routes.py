import os
import unittest
from unittest.mock import patch

# account_factory_server installs a stable `acp` package alias for worktrees
# whose checkout directory is not literally named `acp`.
import account_factory_server  # noqa: F401
from acp.web.server import create_app


class WebFactoryOAuthRoutesTests(unittest.TestCase):
    def test_web_app_exposes_factory_threads_callback(self):
        with patch.dict(
            os.environ,
            {
                "ACP_ENV": "",
                "ACP_ADMIN_PASSWORD": "",
                "ACP_ADAPTER": "mock",
                "ACP_SOURCE": "mock",
                "ACP_CAPTION_LLM": "",
            },
            clear=False,
        ):
            app = create_app()

        callback = "/oauth/account-factory/threads/callback"
        self.assertIn(callback, {rule.rule for rule in app.url_map.iter_rules()})

        response = app.test_client().get(callback)
        self.assertEqual(400, response.status_code)


if __name__ == "__main__":
    unittest.main()
