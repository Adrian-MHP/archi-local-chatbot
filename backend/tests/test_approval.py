"""Offline tests for the approval-mode detection (no Archi / MCP / Azure needed).

A write reports Archi's approval mode only when Archi actually queued it as a proposal; the
collector is per request (thread) so concurrent requests never see each other's proposals.
"""

from __future__ import annotations

import itertools
import threading
import unittest

from app.azure_agent import ChatService, _tracks_proposals


class FakeMcp:
    """bulk-mutate as the Archi plugin answers it: applied, or queued as a proposal."""

    def __init__(self, approval_mode: bool):
        self.approval_mode = approval_mode
        self.ids = itertools.count(1)

    def call_tool(self, name, args):
        if self.approval_mode:
            return {"structuredContent": {"proposal": {"proposalId": f"p-{next(self.ids)}", "status": "pending"}}}
        return {"structuredContent": {"allSucceeded": True, "results": []}}


class Service(ChatService):
    def __init__(self, approval_mode: bool):  # no Azure or MCP connection
        self._proposal_local = threading.local()
        self.mcp = FakeMcp(approval_mode)

    @_tracks_proposals
    def write(self, calls: int = 1):
        for _ in range(calls):
            self._mcp_call("bulk-mutate", {"operations": []}, [])
        return {"summary": "Done." + self.approval_note(self._current_proposals())}

    @_tracks_proposals
    def nested_write(self):
        inner = self.write()
        self._mcp_call("bulk-mutate", {"operations": []}, [])
        return {"inner": inner}


class ApprovalModeTests(unittest.TestCase):
    def test_applied_write_has_no_approval_note(self):
        self.assertEqual(Service(approval_mode=False).write(), {"summary": "Done.", "proposals": []})

    def test_queued_write_reports_its_proposals(self):
        result = Service(approval_mode=True).write(calls=2)
        self.assertEqual(result["proposals"], ["p-1", "p-2"])
        self.assertIn("approval mode is on", result["summary"])
        self.assertIn("p-1, p-2", result["summary"])

    def test_nested_writes_share_one_collector_that_closes_afterwards(self):
        service = Service(approval_mode=True)
        self.assertEqual(service.nested_write()["proposals"], ["p-1", "p-2"])
        self.assertEqual(service._current_proposals(), [])
        self.assertEqual(service.write()["proposals"], ["p-3"])

    def test_concurrent_requests_keep_their_own_proposals(self):
        service = Service(approval_mode=True)
        both_inside = threading.Barrier(2)
        call_tool = service.mcp.call_tool

        def overlapping_call(name, args):
            both_inside.wait(timeout=5)  # both requests are inside their collector at the same time
            return call_tool(name, args)

        service.mcp.call_tool = overlapping_call
        results = {}
        threads = [threading.Thread(target=lambda tag=tag: results.__setitem__(tag, service.write()["proposals"])) for tag in "ab"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual((len(results["a"]), len(results["b"])), (1, 1))
        self.assertEqual(sorted(results["a"] + results["b"]), ["p-1", "p-2"])

    def test_note_is_empty_without_proposals(self):
        self.assertEqual(ChatService.approval_note(None), "")
        self.assertEqual(ChatService.approval_note([]), "")


if __name__ == "__main__":
    unittest.main()
