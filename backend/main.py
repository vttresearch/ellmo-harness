import threading
import logging

from UI import SimpleChatUI
import config
from session import ConversationSession, probe_model_hosts
from tool_catalog import ToolCatalog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


class ChatApp:
    """Controller for coordinating the model backend with the Tkinter view.

    This is now a *thin adapter* around ConversationSession, which owns the
    core chat logic, tool execution, and persistence.
    """

    def __init__(
            self,
            view,
            model_hosts,
            available_functions,
            context_window_size=131072,
            tool_provider=None,
            system_message="",
    ):
        self.view = view
        self.model_hosts = model_hosts
        self.available_functions = available_functions
        self.context_window_size = context_window_size
        self.tool_provider = tool_provider
        self.system_message = system_message

        # UI-agnostic backend session
        self.session = ConversationSession(
            model_hosts=self.model_hosts,
            available_functions=self.available_functions,
            context_window_size=self.context_window_size,
            tool_provider=self.tool_provider,
            system_message=self.system_message,
        )

        # Wire the view's send callback to the controller handler
        self.view.on_send = self.handle_user_message

        # Wire the view's clear-chat callback to reset the conversation
        self.view.on_clear_chat = self.reset_conversation

        # Wire the view's history callback
        if hasattr(self.view, "on_open_history"):
            self.view.on_open_history = self.open_history_dialog

        # Wire the view's host-refresh callback
        if hasattr(self.view, "on_refresh_hosts"):
            self.view.on_refresh_hosts = self.refresh_hosts

        # Configure host dropdown via the view
        host_names = list(self.model_hosts.keys())
        self.view.host_combo["values"] = host_names
        if host_names:
            self.view.host_var.set(host_names[0])

        # Prepare model data structures so the UI can render immediately.
        self.host_models = {name: [] for name in host_names}
        self.host_models_without_tools = {name: [] for name in host_names}
        self.manual_models = {}

        # Show a non-blocking placeholder while host/model probing runs.
        try:
            self.view.model_combo["values"] = ["Loading models..."]
            self.view.model_var.set("Loading models...")
        except Exception:
            pass

        # Update models whenever the user changes the selected host
        if hasattr(self.view, "host_var"):
            try:
                self.view.host_var.trace_add(
                    "write",
                    lambda *args: self._update_model_list_for_selected_host(),
                )
            except Exception:
                pass

        if hasattr(self.view, "host_combo"):
            try:
                self.view.host_combo.bind(
                    "<<ComboboxSelected>>",
                    lambda event: self._update_model_list_for_selected_host(),
                )
            except Exception:
                pass

        if hasattr(self.view, "model_var"):
            try:
                self.view.model_var.trace_add(
                    "write",
                    lambda *args: self._remember_model_for_selected_host(),
                )
            except Exception:
                pass

        # Probe all configured hosts for availability and list their models
        # in the background so the window opens immediately.
        self._probe_hosts_async()

    # === Host/model probing ===

    def _probe_hosts_async(self) -> None:
        """Run host probing on a worker thread and apply results on Tk thread."""
        worker = threading.Thread(target=self._probe_hosts_worker, daemon=True)
        worker.start()

    def refresh_hosts(self) -> None:
        """Refresh host/model availability without restarting the app."""
        try:
            self.view.model_combo["values"] = ["Loading models..."]
            self.view.model_var.set("Loading models...")
        except Exception:
            pass
        self._probe_hosts_async()

    def _probe_hosts_worker(self) -> None:
        """Background worker for host probing."""
        try:
            result = probe_model_hosts(self.model_hosts)
        except Exception as e:
            self.view.after(0, lambda err=e: self._apply_probe_error(err))
            return

        self.view.after(0, lambda: self._apply_probe_result(result))

    def _apply_probe_error(self, error: Exception) -> None:
        """Apply probe failure state on the UI thread."""
        if hasattr(self.view, "append_log"):
            self.view.append_log(f"Error probing hosts: {error}\n")
        try:
            self.view.model_combo["values"] = []
            self.view.model_var.set("")
        except Exception:
            pass

    def _apply_probe_result(self, result) -> None:
        """Apply host probe results and refresh model dropdown on the UI thread."""
        self.host_models = {}
        self.host_models_without_tools = {}

        for name, info in result.items():
            online = bool(info.get("online"))
            error = info.get("error")
            provider = str(info.get("provider") or "unknown").strip()

            if not online:
                if hasattr(self.view, "append_log"):
                    msg = f"[{name}] {provider} unavailable"
                    if error:
                        msg += f": {error}"
                    self.view.append_log(msg + "\n")
                # Still record empty model lists for UI consistency
                self.host_models[name] = []
                self.host_models_without_tools[name] = []
                continue

            tool_models = info.get("tool_models", []) or []
            no_tool_models = info.get("no_tool_models", []) or []

            self.host_models[name] = tool_models
            self.host_models_without_tools[name] = no_tool_models

            if hasattr(self.view, "append_log"):
                if tool_models:
                    model_list = ", ".join(tool_models)
                    self.view.append_log(
                        f"[{name}] {provider} available. Models with tool support: {model_list}\n"
                    )
                else:
                    note = str(error or "").strip()
                    if note:
                        self.view.append_log(f"[{name}] {provider} available. {note}\n")
                    else:
                        self.view.append_log(
                            f"[{name}] {provider} available, but no tool-enabled models were found.\n"
                        )

                if no_tool_models:
                    disabled_list = ", ".join(no_tool_models)
                    self.view.append_log(
                        f"[{name}] Models without tool support (disabled in UI): {disabled_list}\n"
                    )

        try:
            self._update_model_list_for_selected_host()
        except Exception:
            pass

    def _update_model_list_for_selected_host(self) -> None:
        """Update the model dropdown based on the currently selected host."""
        # Ensure we have probed hosts and the view has the expected widgets
        if not hasattr(self, "host_models"):
            return
        if not hasattr(self.view, "model_combo") or not hasattr(self.view, "model_var"):
            return
        if not hasattr(self.view, "host_var"):
            return

        selected_name = self.view.host_var.get()
        models = self.host_models.get(selected_name, []) or []
        remembered_model = self.manual_models.get(selected_name, "")
        default_model = str(
            (self.model_hosts.get(selected_name, {}) or {}).get("default_model", "")
        ).strip()
        preferred_model = remembered_model or default_model

        # Update the combobox values
        self.view.model_combo["values"] = models

        # Keep the current selection if still valid; otherwise pick the first model
        current = self.view.model_var.get()
        if current in models:
            return

        if models:
            self.view.model_var.set(models[0])
        else:
            self.view.model_var.set(preferred_model)

    def _remember_model_for_selected_host(self) -> None:
        """Persist the most recent model text per selected host."""
        if not hasattr(self.view, "host_var") or not hasattr(self.view, "model_var"):
            return

        selected_name = (self.view.host_var.get() or "").strip()
        if not selected_name:
            return

        model_text = (self.view.model_var.get() or "").strip()
        if model_text:
            self.manual_models[selected_name] = model_text
        else:
            self.manual_models.pop(selected_name, None)

    # === High-level conversation flow ===

    def _save_conversation_snapshot(self) -> None:
        """Persist the current conversation state to disk via ConversationSession.

        We keep this helper on the Tk adapter so it can harvest the current
        visible transcripts from the UI, while the backend session handles
        the actual ConversationManager interaction.
        """
        try:
            chat_transcript = (
                self.view.get_chat_transcript()
                if hasattr(self.view, "get_chat_transcript")
                else ""
            )
        except Exception:
            chat_transcript = ""

        try:
            log_text = (
                self.view.get_log_text()
                if hasattr(self.view, "get_log_text")
                else ""
            )
        except Exception:
            log_text = ""

        try:
            self.session.save_snapshot(chat_transcript, log_text)
        except Exception:
            # Non-fatal
            pass

    def open_history_dialog(self) -> None:
        """Open the History dialog and allow the user to pick a conversation."""
        try:
            conversations = self.session.list_conversations()
        except Exception as e:
            if hasattr(self.view, "append_log"):
                self.view.append_log(f"Error listing conversations: {e}\n")
            return

        if not conversations:
            if hasattr(self.view, "append_log"):
                self.view.append_log("No saved conversations found.\n")
            return

        def _on_select(conv_id: str) -> None:
            if conv_id:
                self.restore_conversation(conv_id)

        try:
            if hasattr(self.view, "show_history_dialog"):
                self.view.show_history_dialog(conversations, _on_select)
        except Exception as e:
            if hasattr(self.view, "append_log"):
                self.view.append_log(f"Error showing history dialog: {e}\n")

    def restore_conversation(self, conv_id: str) -> None:
        """Restore a conversation by id using the backend session."""
        data = self.session.restore_conversation(conv_id)
        if not data:
            if hasattr(self.view, "append_log"):
                self.view.append_log(f"Conversation not found: {conv_id}\n")
            return

        # Restore UI transcript and logs
        chat_transcript = data.get("chat_transcript") or ""
        log_text = data.get("log_text") or ""
        messages = data.get("messages") or []

        try:
            if hasattr(self.view, "set_chat_messages") and isinstance(messages, list) and messages:
                self.view.set_chat_messages(messages)
            elif hasattr(self.view, "set_chat_transcript"):
                self.view.set_chat_transcript(chat_transcript)
        except Exception:
            pass

        try:
            if hasattr(self.view, "set_log_text"):
                self.view.set_log_text(log_text)
        except Exception:
            pass

        # Restore host/model selection if still available
        host = data.get("host") or ""
        model = data.get("model") or ""

        try:
            if hasattr(self.view, "set_host_and_model"):
                self.view.set_host_and_model(host, model)
        except Exception:
            pass

        # Reset progress bar
        try:
            if hasattr(self.view, "update_progress"):
                self.view.update_progress(0.0, 0.0)
        except Exception:
            pass

        # Log restoration
        title = data.get("title") or data.get("id") or conv_id
        try:
            if hasattr(self.view, "append_log"):
                self.view.append_log(f"Restored conversation: {title}\n")
        except Exception:
            pass

    def handle_user_message(self, msg: str) -> None:
        """Handle a user message coming from the view.

        The view has already displayed the user's message; here we only
        update backend state and launch the background worker.
        """
        if not msg:
            return

        # Run the model call in a background thread so the UI stays responsive
        worker = threading.Thread(
            target=self._handle_conversation_turn,
            args=(msg,),
            daemon=True,
        )
        worker.start()

    def reset_conversation(self) -> None:
        """Reset the current conversation state.

        This clears the in-memory message history so that subsequent
        messages start a fresh conversation with the model.
        """
        self.session.reset()
        try:
            self.view.append_log("Conversation reset by user.\n")
        except Exception:
            # If logging fails for some reason, ignore
            pass

    # === Internal helpers ===

    def _format_tool_output_log(self, tool_name: str, tool_arguments: dict, tool_output: str) -> str:
        """Format tool output as a single log entry block with tool metadata."""
        name = (tool_name or "").strip() or "unknown_tool"
        args = tool_arguments if isinstance(tool_arguments, dict) else {}
        normalized = str(tool_output or "").replace("\r\n", "\n").replace("\r", "\n")
        if not normalized:
            normalized = "(empty)"

        if args:
            arg_parts = []
            for key, value in args.items():
                if isinstance(value, str):
                    value_text = value
                else:
                    value_text = str(value)
                arg_parts.append(f"{key}='{value_text}'")
            args_text = ", ".join(arg_parts)
            header = f"Tool output for '{name}', {args_text}"
        else:
            header = f"Tool output for '{name}'"

        return header + "\n" + normalized

    def _handle_conversation_turn(self, user_message: str) -> None:
        """Execute one full assistant turn via ConversationSession.

        This runs in a background thread. All UI updates are marshalled back
        to the main thread through the view's helper methods.
        """
        # Determine which host/model to use based on the current UI selection
        host = None
        model = None
        try:
            if hasattr(self.view, "host_var"):
                host = (self.view.host_var.get() or "").strip()
        except Exception:
            host = None

        try:
            if hasattr(self.view, "model_var"):
                model = (self.view.model_var.get() or "").strip()
        except Exception:
            model = None

        # Configure backend session and perform a step
        self.session.configure(host, model)

        def _on_event(ev: dict) -> None:
            if ev.get("type") != "agent_message":
                return
            data = ev.get("data") or {}
            stats = data.get("stats") or {}
            try:
                if hasattr(self.view, "update_progress"):
                    prompt_ratio = float(stats.get("context_fullness") or 0.0)
                    total_ratio = float(stats.get("context_fullness_with_output") or 0.0)
                    self.view.update_progress(prompt_ratio, total_ratio)
            except Exception:
                pass

            # Stream tool call info to the log immediately per event
            try:
                if hasattr(self.view, "append_log"):
                    kind = data.get("kind") or ""
                    if kind == "tool_call":
                        tool_calls = data.get("tool_calls") or []
                        if tool_calls:
                            for tc in tool_calls:
                                tool_name = tc.get("tool_name") or "unknown_tool"
                                self.view.append_log(f"Tool calls: {tool_name}\n")
                    elif kind == "tool_result":
                        tool_name = data.get("tool_name") or "unknown_tool"
                        tool_arguments = data.get("tool_arguments") or {}
                        raw_tool_output = data.get("tool_output")
                        tool_output = "" if raw_tool_output is None else str(raw_tool_output)
                        self.view.append_log(
                            self._format_tool_output_log(tool_name, tool_arguments, tool_output)
                        )
                    elif kind == "assistant_message":
                        content = str(data.get("content") or "").strip()
                        if content:
                            self.view.append_log("Agent message:\n")

                    if kind != "tool_result":
                        log_data = data.get("log") or ""
                        if isinstance(log_data, dict):
                            try:
                                import json as _json
                                log_text = _json.dumps(log_data, indent=2, sort_keys=True)
                            except Exception:
                                log_text = str(log_data)
                        else:
                            log_text = str(log_data) if log_data is not None else ""
                        if log_text:
                            self.view.append_log(log_text)
            except Exception:
                pass

        result = self.session.step(user_message, on_event=_on_event)

        stats = result.get("stats") or {}
        events = result.get("events") or []

        # Update progress bar based on stats
        try:
            if hasattr(self.view, "update_progress"):
                prompt_ratio = float(stats.get("context_fullness") or 0.0)
                total_ratio = float(stats.get("context_fullness_with_output") or 0.0)
                self.view.update_progress(prompt_ratio, total_ratio)
        except Exception:
            pass

        # Apply event stream to the Tk UI
        # The backend emits `agent_message` events only.
        for ev in events:
            if ev.get("type") != "agent_message":
                continue

            payload = ev.get("data") or {}
            kind = payload.get("kind") or ""

            # Log payloads are streamed in real time via the on_event callback

            # Show assistant content in the chat panel (if any)
            content = payload.get("content") or ""
            if content and hasattr(self.view, "show_bot_message"):
                self.view.show_bot_message(content)

            # Tool calls are streamed in real time via the on_event callback

        # Schedule snapshot after tools are done and bot reply is shown
        try:
            self.view.after(0, self._save_conversation_snapshot)
        except Exception:
            pass


if __name__ == "__main__":
    context_window_size = config.get_context_window_size()
    system_message = config.get_system_message()

    tool_catalog = ToolCatalog()
    available_functions = tool_catalog.get_enabled_tool_functions()

    model_hosts = config.get_model_hosts()

    voice_input = config.load_config().get("voice_input")
    voice_server_url = ""
    if isinstance(voice_input, dict) and voice_input.get("enabled") is True:
        voice_server_url = str(voice_input.get("server_url") or "http://127.0.0.1:5050").strip()

    view = SimpleChatUI(voice_server_url=voice_server_url)
    app = ChatApp(
        view=view,
        model_hosts=model_hosts,
        available_functions=available_functions,
        context_window_size=context_window_size,
        tool_provider=tool_catalog.get_enabled_tool_functions,
        system_message=system_message,
    )
    view.mainloop()
