# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional


class OpenAITraceCollector:
    """Minimal trace collector for OpenAI Responses streams.

    - .tool decorator to wrap tools and record call/result events
    - .ingest_event(event) to record raw events (coalesced by default)
    - .process_stream(stream, tools) to run a streaming response and execute tools
    - .events holds a simple list of recorded events for persistence
    - .model_config holds model configuration for risk evaluation
    """

    def __init__(self, coalesce: bool = True):
        self.events: List[Dict[str, Any]] = []
        self.coalesce = coalesce
        self._args_buf: Dict[str, str] = {}
        self._call_meta: Dict[str, Dict[str, Any]] = {}
        self._reasoning_buf: List[str] = []
        self.model_config: Dict[str, Any] = {}
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None

    def set_model_config(
        self,
        *,
        provider: str = "openai",
        model: str,
        tools_enabled: Optional[List[str]] = None,
        **kwargs: Any
    ) -> None:
        """Set model configuration for trace context."""
        self.model_config = {
            "provider": provider,
            "model": model,
            "tools_enabled": tools_enabled or [],
            **kwargs
        }

    def record_user_input(self, content: str, role: str = "user") -> None:
        """Record user input for risk evaluation."""
        import hashlib
        self.events.insert(0, {
            "ts": time.time(),
            "type": "user_input",
            "role": role,
            "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "length": len(content)
        })
        if self.started_at is None:
            self.started_at = time.time()

    def record_system_prompt(self, content: str, version: str = "v1.0") -> None:
        """Record system prompt for verification."""
        import hashlib
        self.events.insert(0, {
            "ts": time.time(),
            "type": "system_prompt",
            "role": "system",
            "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "version": version,
            "length": len(content)
        })

    def record_agent_output(self, content: str, role: str = "assistant") -> None:
        """Record agent final output for compliance checking."""
        import hashlib
        self.events.append({
            "ts": time.time(),
            "type": "agent_output",
            "role": role,
            "content": content,
            "output_hash": hashlib.sha256(content.encode()).hexdigest(),
            "length": len(content)
        })
        if self.completed_at is None:
            self.completed_at = time.time()

    def tool(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(fn):
            async def _aw(*a, **k):
                self.events.append({"ts": time.time(), "type": "tool_call", "name": fn.__name__, "args": {"a": a, "k": k}})
                out = await fn(*a, **k)
                self.events.append({"ts": time.time(), "type": "tool_result", "name": fn.__name__, "result": out})
                return out

            return _aw
        else:
            def _sw(*a, **k):
                self.events.append({"ts": time.time(), "type": "tool_call", "name": fn.__name__, "args": {"a": a, "k": k}})
                out = fn(*a, **k)
                self.events.append({"ts": time.time(), "type": "tool_result", "name": fn.__name__, "result": out})
                return out

            return _sw

    def ingest_event(self, event: Any) -> None:
        t = getattr(event, "type", None)
        now = time.time()
        if not self.coalesce:
            self.events.append({"ts": now, "type": t})
            return

        if t == "response.output_item.added":
            it = getattr(event, "item", None)
            if it and getattr(it, "type", None) == "function_call":
                cid = getattr(it, "call_id", None)
                name = getattr(it, "name", None)
                if cid and name:
                    self._call_meta[cid] = {"name": name}
                    self._args_buf[cid] = ""
            return

        if t == "response.function_call_arguments.delta":
            cid = getattr(event, "call_id", None)
            delta = getattr(event, "delta", "")
            if cid:
                self._args_buf[cid] = self._args_buf.get(cid, "") + delta
            return

        if t in ("response.function_call_arguments.done", "response.output_item.done"):
            it = getattr(event, "item", None)
            if it and getattr(it, "type", None) == "function_call":
                cid = getattr(it, "call_id", None)
                name = getattr(it, "name", None)
                args_str = getattr(it, "arguments", None) or self._args_buf.get(cid, "")
                if name:
                    try:
                        args = json.loads(args_str or "{}")
                    except Exception:
                        args = {"_raw": args_str}
                    self.events.append({"ts": now, "type": "function_call", "name": name, "call_id": cid, "arguments": args})
                if cid:
                    self._args_buf.pop(cid, None)
                    self._call_meta.pop(cid, None)
            return

        if t == "response.reasoning_summary_text.delta":
            self._reasoning_buf.append(getattr(event, "delta", ""))
            return

        if t == "response.reasoning_summary_text.done":
            text = getattr(event, "text", None) or "".join(self._reasoning_buf)
            self.events.append({"ts": now, "type": "reasoning_summary", "text": text})
            self._reasoning_buf.clear()
            return

        if t in ("response.created", "response.completed"):
            self.events.append({"ts": now, "type": t})
            return

    async def process_stream(self, stream: Any, *, tools: Dict[str, Callable[..., Any]]) -> Dict[str, Any]:
        tool_results: Dict[str, Any] = {}
        arg_buffer: Dict[str, str] = {}
        call_metadata: Dict[str, Dict[str, Any]] = {}

        for event in stream:
            self.ingest_event(event)
            et = getattr(event, "type", None)
            if et == "response.output_item.added":
                it = getattr(event, "item", None)
                if it and getattr(it, "type", None) == "function_call":
                    cid = getattr(it, "call_id", None)
                    name = getattr(it, "name", None)
                    if cid and name:
                        call_metadata[cid] = {"name": name}
                        arg_buffer[cid] = ""
            elif et == "response.function_call_arguments.delta":
                cid = getattr(event, "call_id", None)
                delta = getattr(event, "delta", "")
                if cid:
                    arg_buffer[cid] = arg_buffer.get(cid, "") + delta
            elif et in ("response.function_call_arguments.done", "response.output_item.done"):
                it = getattr(event, "item", None)
                cid = getattr(it, "call_id", None) if it else getattr(event, "call_id", None)
                if cid in call_metadata:
                    tool_name = call_metadata[cid]["name"]
                    args_str = getattr(it, "arguments", None) if it else None
                    if not args_str:
                        args_str = arg_buffer.get(cid, "")
                    try:
                        args = json.loads(args_str or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    fn = tools[tool_name]
                    if asyncio.iscoroutinefunction(fn):
                        result = await fn(**args)
                    else:
                        result = fn(**args)
                    tool_results[tool_name] = result
                    arg_buffer.pop(cid, None)
                    call_metadata.pop(cid, None)
        _ = stream.get_final_response()
        return {"tool_results": tool_results}

