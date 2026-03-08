import unittest

from agent_memory.adapters import ClaudeAdapter, CodexAdapter, OpenCodeAdapter


class AdapterTests(unittest.TestCase):
    def test_claude_prompt_normalization(self):
        adapter = ClaudeAdapter()
        [event] = adapter.normalize({
            "event": "UserPromptSubmit",
            "session_id": "s1",
            "project": "proj",
            "timestamp": "2026-03-08T00:00:00Z",
            "prompt": "hello",
        })
        self.assertEqual(event.kind, "prompt")
        self.assertEqual(event.agent, "claude")
        self.assertEqual(event.content, "hello")

    def test_codex_tool_normalization(self):
        adapter = CodexAdapter()
        [event] = adapter.normalize({
            "type": "function_call",
            "session_id": "s2",
            "cwd": "proj",
            "name": "shell",
            "arguments": "{\"command\":[\"bash\",\"-lc\",\"pwd\"]}",
        })
        self.assertEqual(event.kind, "tool_call")
        self.assertEqual(event.tool_name, "shell")
        self.assertEqual(event.content, "bash -lc pwd")

    def test_codex_history_prompt_normalization(self):
        adapter = CodexAdapter()
        [event] = adapter.normalize({
            "session_id": "s-history",
            "ts": 1754609319,
            "text": "remember this prompt",
        })
        self.assertEqual(event.kind, "prompt")
        self.assertEqual(event.role, "user")
        self.assertEqual(event.content, "remember this prompt")
        self.assertTrue(event.ts.endswith("Z"))

    def test_codex_post_tool_hook_emits_call_and_result(self):
        adapter = CodexAdapter()
        events = adapter.normalize({
            "tool": "Edit",
            "session": "s2",
            "cwd": "proj",
            "ts": 1754609319,
            "file": "src/app.py",
            "tool_input": {"file_path": "src/app.py", "old_string": "a", "new_string": "b"},
            "response": "updated file",
        })
        self.assertEqual([event.kind for event in events], ["tool_call", "tool_result"])
        self.assertEqual(events[0].tool_name, "Edit")
        self.assertEqual(events[0].target_path, "src/app.py")
        self.assertEqual(events[1].content, "updated file")

    def test_opencode_file_normalization(self):
        adapter = OpenCodeAdapter()
        [event] = adapter.normalize({
            "event": "file.edited",
            "sessionId": "s3",
            "projectId": "proj",
            "path": "src/app.py",
        })
        self.assertEqual(event.kind, "file_touch")
        self.assertEqual(event.target_path, "src/app.py")

    def test_opencode_chat_message_normalization(self):
        adapter = OpenCodeAdapter()
        [event] = adapter.normalize({
            "type": "chat.message",
            "sessionID": "ses_1",
            "projectId": "proj",
            "message": {
                "id": "msg_1",
                "sessionID": "ses_1",
                "role": "user",
            },
            "parts": [
                {"type": "text", "text": "hello from opencode"},
            ],
        })
        self.assertEqual(event.kind, "prompt")
        self.assertEqual(event.content, "hello from opencode")

    def test_opencode_tool_after_normalization(self):
        adapter = OpenCodeAdapter()
        [event] = adapter.normalize({
            "type": "tool.execute.after",
            "sessionID": "ses_1",
            "projectId": "proj",
            "tool": "edit",
            "callID": "call_1",
            "output": "done",
            "metadata": {"filePath": "src/app.py"},
        })
        self.assertEqual(event.kind, "tool_result")
        self.assertEqual(event.tool_name, "edit")
        self.assertEqual(event.content, "done")

    def test_opencode_export_document_normalization(self):
        adapter = OpenCodeAdapter()
        events = adapter.normalize({
            "info": {
                "id": "ses_1",
                "directory": "/tmp/project",
                "projectID": "global",
                "title": "sample",
                "time": {"created": 1772764212176},
            },
            "messages": [
                {
                    "info": {
                        "id": "msg_user",
                        "sessionID": "ses_1",
                        "role": "user",
                        "time": {"created": 1772764212220},
                        "summary": {"diffs": []},
                        "agent": "Default",
                        "model": {"providerID": "x", "modelID": "y"},
                    },
                    "parts": [{"id": "p1", "sessionID": "ses_1", "messageID": "msg_user", "type": "text", "text": "hi"}],
                },
                {
                    "info": {
                        "id": "msg_assistant",
                        "sessionID": "ses_1",
                        "role": "assistant",
                        "time": {"created": 1772764212233, "completed": 1772764212703},
                        "agent": "Default",
                        "providerID": "x",
                        "modelID": "y",
                    },
                    "parts": [
                        {"id": "step1", "sessionID": "ses_1", "messageID": "msg_assistant", "type": "step-start"},
                        {"id": "text1", "sessionID": "ses_1", "messageID": "msg_assistant", "type": "text", "text": "hello there"},
                        {
                            "id": "tool1",
                            "sessionID": "ses_1",
                            "messageID": "msg_assistant",
                            "type": "tool",
                            "callID": "call_1",
                            "tool": "write",
                            "state": {
                                "status": "completed",
                                "input": {"filePath": "src/app.py"},
                                "output": "updated",
                                "title": "Write file",
                                "metadata": {},
                                "time": {"start": 1772764212235, "end": 1772764212236},
                            },
                        },
                        {
                            "id": "patch1",
                            "sessionID": "ses_1",
                            "messageID": "msg_assistant",
                            "type": "patch",
                            "hash": "abc123",
                            "files": ["src/app.py"],
                        },
                    ],
                },
            ],
        })
        kinds = sorted(event.kind for event in events)
        self.assertEqual(
            kinds,
            ["assistant_message", "file_touch", "prompt", "session_state", "tool_call", "tool_result"],
        )
        tool_call = next(event for event in events if event.kind == "tool_call")
        self.assertEqual(tool_call.tool_name, "write")
        self.assertEqual(tool_call.target_path, "src/app.py")


if __name__ == "__main__":
    unittest.main()
