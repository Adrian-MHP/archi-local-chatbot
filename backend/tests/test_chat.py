"""Offline tests for the chat loop (no Azure / Archi needed): live events while streaming, approval
proposals from tool calls, and stopping before the next model or tool call."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace as NS

from app.azure_agent import ChatService, ChatStopped


def call(id_, name, arguments="{}"):
    return {"id": id_, "name": name, "arguments": arguments}


class FakeCompletions:
    """Plays one scripted round per model call: {"text": str, "calls": [...]}, streamed or not."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        round_ = self.rounds.pop(0)
        text, calls = round_.get("text", ""), round_.get("calls", [])
        if not kwargs.get("stream"):
            tool_calls = [NS(id=c["id"], function=NS(name=c["name"], arguments=c["arguments"])) for c in calls] or None
            return NS(choices=[NS(message=NS(content=text, tool_calls=tool_calls))])

        def chunks():
            yield NS(choices=[])  # Azure's content-filter preamble
            for word in text.split(" ") if text else []:
                yield NS(choices=[NS(delta=NS(content=word + " ", tool_calls=None))])
            for index, c in enumerate(calls):
                # name and arguments arrive in pieces, like the real API
                yield NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(index=index, id=c["id"], function=NS(name=c["name"], arguments=""))]))])
                yield NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(index=index, id=None, function=NS(name=None, arguments=c["arguments"]))]))])

        return chunks()


class FakeMcp:
    def __init__(self, approval_mode=False):
        self.approval_mode = approval_mode
        self.calls = []

    def list_tools(self):
        return [NS(name=n, description=n, input_schema={"type": "object", "properties": {}})
                for n in ("get-model-info", "bulk-mutate")]

    def call_tool(self, name, args):
        self.calls.append(name)
        if name == "bulk-mutate" and self.approval_mode:
            return {"structuredContent": {"proposal": {"proposalId": "p-7", "status": "pending"}}}
        return {"structuredContent": {"name": "Archimate MCP TEST"}}


class Service(ChatService):
    def __init__(self, rounds, approval_mode=False):  # no Azure or MCP connection
        self.settings = NS(azure_openai_api_key="k", azure_openai_base_url="u", azure_openai_model="m",
                           azure_openai_fallback_model=None, model_temperature=0.2, max_model_retries=0,
                           model_retry_backoff_seconds=0, max_tool_roundtrips=5, default_system_prompt="You help.")
        self._proposal_local = threading.local()
        self.mcp = FakeMcp(approval_mode)
        self.completions = FakeCompletions(rounds)
        self.openai = NS(chat=NS(completions=self.completions))


def run(service, **kwargs):
    events = []
    result = service.chat_turn(history=[], message="Which model is active?",
                               on_event=lambda name, data: events.append((name, data)), **kwargs)
    return result, events


class ChatLoopTests(unittest.TestCase):
    def test_streamed_answer_after_a_tool_call(self):
        service = Service([{"text": "Let me check.", "calls": [call("c1", "get-model-info")]},
                           {"text": "The active model is **Archimate MCP TEST**."}])
        result, events = run(service)
        self.assertEqual(result["answer"], "The active model is **Archimate MCP TEST**. ")
        self.assertEqual(result["used_tools"], ["get-model-info"])
        self.assertEqual(result["proposals"], [])
        names = [name for name, _ in events]
        reset = names.index("draft_reset")
        self.assertEqual(names[0], "round")
        self.assertEqual(set(names[1:reset]), {"delta"})                       # the preamble streams ...
        self.assertEqual(names[reset:reset + 4], ["draft_reset", "tool_start", "tool_end", "round"])  # ... and is withdrawn
        self.assertEqual(set(names[reset + 4:]), {"delta"})
        self.assertTrue(all(r.get("stream") for r in service.completions.requests))

    def test_without_events_the_model_is_not_streamed(self):
        service = Service([{"calls": [call("c1", "get-model-info")]}, {"text": "Archimate MCP TEST"}])
        result = service.chat_turn(history=[], message="Which model?")
        self.assertEqual(result["answer"], "Archimate MCP TEST")
        self.assertFalse(any(r.get("stream") for r in service.completions.requests))

    def test_approval_proposals_are_reported(self):
        service = Service([{"calls": [call("c1", "bulk-mutate", '{"operations": []}')]}, {"text": "Proposed."}],
                          approval_mode=True)
        result, events = run(service)
        self.assertEqual(result["proposals"], ["p-7"])
        tool_end = next(data for name, data in events if name == "tool_end")
        self.assertEqual((tool_end["name"], tool_end["ok"], tool_end["proposal"]), ("bulk-mutate", True, "p-7"))

    def test_stop_prevents_the_next_tool_call(self):
        service = Service([{"calls": [call("c1", "get-model-info"), call("c2", "bulk-mutate")]}, {"text": "never"}])
        stop = threading.Event()

        def on_event(name, data):
            if name == "tool_end":
                stop.set()  # the user presses Stop after the first tool call

        with self.assertRaises(ChatStopped):
            service.chat_turn(history=[], message="Change the model", on_event=on_event, should_stop=stop.is_set)
        self.assertEqual(service.mcp.calls, ["get-model-info"])  # bulk-mutate never ran
        self.assertEqual(len(service.completions.requests), 1)    # no further model call


if __name__ == "__main__":
    unittest.main()
