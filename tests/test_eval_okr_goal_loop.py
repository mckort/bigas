"""OKR goal-loop eval pack — frozen fixture, same tools as production."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from bigas.eval.base import EvalFixture, get_use_case_evaluator, list_use_cases
from bigas.eval.checks import run_mechanical_checks
from bigas.eval.pack import load_pack
from bigas.eval.use_cases.okr_goal_loop import OkrGoalLoopEvaluator
from bigas.okr.loop import GoalLoopResult


class OkrGoalLoopPackTests(unittest.TestCase):
    def test_pack_and_registry(self):
        pack = load_pack("okr-goal-loop")
        self.assertEqual(pack.baseline_model, "gemini:gemini-3.8-flash")
        self.assertEqual([step.id for step in pack.steps], ["research", "plan", "followup"])
        self.assertIn("okr-goal-loop", list_use_cases())
        self.assertIsInstance(get_use_case_evaluator("okr-goal-loop"), OkrGoalLoopEvaluator)

    def test_fixture_is_public_snapshot(self):
        evaluator = OkrGoalLoopEvaluator()
        fixture = evaluator.default_fixture()
        self.assertEqual(fixture.company_name, "Green Promo Wear")
        self.assertIn("sessions 43", fixture.input_text)
        self.assertNotIn("workspaceId", fixture.input_text)
        self.assertNotIn("companyId", fixture.input_text)


class OkrMechanicalCheckTests(unittest.TestCase):
    def _output(self, *, krs, tasks, follow=None):
        steps = {
            "research": json.dumps({"key_results": krs}),
            "plan": json.dumps({"tasks": tasks}),
            "followup": json.dumps({"tasks": follow or []}),
        }
        return {
            "pack_id": "okr-goal-loop",
            "steps": steps,
            "evidence": {"ga4": "sessions 43 conversion 5"},
            "sources": {"page": "sessions 43 conversion 5", "snippets": "GPWW-1 Update catalog"},
        }

    def test_penalizes_saas_kit_and_kr_clone(self):
        check = run_mechanical_checks(
            self._output(
                krs=[
                    {"title": "40 weekly active founders", "measurable": True, "baseline": 12},
                    {
                        "title": "Increase website sessions from 43 to 1000",
                        "measurable": True,
                        "baseline": 43,
                    },
                ],
                tasks=[{"title": "Increase website sessions from 43 to 1000"}],
            ),
            EvalFixture("Green Promo Wear", "https://greenpromowear.com", input_text="sessions 43"),
            required_steps=("research", "plan", "followup"),
        )
        self.assertGreater(check.penalty, 0)
        blob = " ".join(check.notes).lower()
        self.assertIn("saas-kit", blob)
        self.assertIn("clone", blob)

    def test_clean_plan_has_no_penalty_for_action_task(self):
        check = run_mechanical_checks(
            self._output(
                krs=[
                    {
                        "title": "Increase sample-to-paid conversion from 5 to 15",
                        "measurable": True,
                        "baseline": 5,
                    },
                    {
                        "title": "Increase sessions from 43 to 80",
                        "measurable": True,
                        "baseline": 43,
                    },
                ],
                tasks=[{"title": "Publish a wholesale kit landing page"}],
            ),
            EvalFixture("Green Promo Wear", "https://greenpromowear.com", input_text="sessions 43 conversion 5"),
            required_steps=("research", "plan", "followup"),
        )
        self.assertEqual(check.penalty, 0)


class OkrGoalLoopRunTests(unittest.TestCase):
    @patch("bigas.eval.use_cases.okr_goal_loop.run_goal_loop")
    def test_run_three_phases(self, mock_loop):
        mock_loop.side_effect = [
            GoalLoopResult(
                key_results=[
                    {
                        "title": "Increase conversion from 5 to 15",
                        "measurable": True,
                        "baseline": 5,
                    }
                ],
                used_tools=True,
                tool_trace=["get_evidence", "propose_key_results", "done"],
            ),
            GoalLoopResult(
                tasks=[{"title": "Publish a wholesale kit landing page", "kr_id": "kr-1"}],
                used_tools=True,
                tool_trace=["list_open_work", "propose_tasks", "done"],
            ),
            GoalLoopResult(tasks=[], used_tools=True, tool_trace=["get_scoreboard", "done"]),
        ]
        evaluator = OkrGoalLoopEvaluator()
        output, usage = evaluator.run(evaluator.default_fixture(), "gemini-3.8-flash")
        self.assertEqual(output["pack_id"], "okr-goal-loop")
        self.assertIn("research", output["steps"])
        self.assertIn("plan", output["steps"])
        self.assertIn("followup", output["steps"])
        self.assertEqual(mock_loop.call_count, 3)
        phases = [call.kwargs["snapshot"].phase for call in mock_loop.call_args_list]
        self.assertEqual(phases, ["research", "plan", "in_progress"])
        self.assertEqual(usage.prompt_tokens, 0)


class AnthropicToolCompleteTests(unittest.TestCase):
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False)
    @patch("bigas.eval.complete.requests.post")
    def test_forwards_tools_and_parses_tool_use(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "get_evidence",
                    "input": {},
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 8, "output_tokens": 3},
        }
        from bigas.eval.complete import complete_eval_chat

        completion = complete_eval_chat(
            "claude-sonnet-5",
            [{"role": "user", "content": "Inspect the goal."}],
            tools=[
                {
                    "type": "function",
                    "function": {"name": "get_evidence", "description": "Evidence", "parameters": {"type": "object"}},
                }
            ],
        )
        self.assertEqual(completion.tool_calls[0].name, "get_evidence")
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["tools"][0]["name"], "get_evidence")


if __name__ == "__main__":
    unittest.main()
