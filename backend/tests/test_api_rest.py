import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import api_rest


class ApiRestHealthTests(unittest.TestCase):
    def test_health_does_not_wait_for_model_host_probes(self) -> None:
        with patch.object(api_rest, "probe_model_hosts") as probe:
            response = asyncio.run(api_rest.health_check())

        probe.assert_not_called()
        self.assertEqual(response["type"], "health")
        self.assertEqual(response["data"], {"status": "ok"})

    def test_hosts_runs_blocking_probes_in_worker_thread(self) -> None:
        hosts = {"local": {"online": True, "tool_models": ["model"]}}
        with patch.object(
            api_rest,
            "run_in_threadpool",
            new=AsyncMock(return_value=hosts),
        ) as run_in_worker:
            response = asyncio.run(api_rest.get_hosts())

        run_in_worker.assert_awaited_once_with(
            api_rest.probe_model_hosts,
            api_rest.MODEL_HOSTS,
        )
        self.assertEqual(response["data"]["hosts"], hosts)


if __name__ == "__main__":
    unittest.main()
