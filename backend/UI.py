import threading
import tkinter as tk
from tkinter import ttk, messagebox
import html
import logging
import re
from queue import Empty

try:
    from tkinterweb import HtmlFrame
except Exception:
    HtmlFrame = None

try:
    import markdown
except Exception:
    markdown = None


def _create_html_frame(parent):
    """Use plain Tk when the optional renderer cannot load its native widget."""
    if HtmlFrame is None:
        return None
    existing = set(parent.winfo_children())
    try:
        return HtmlFrame(parent)
    except (OSError, tk.TclError) as exc:
        # A failed constructor can leave a partially created frame behind.
        for widget in set(parent.winfo_children()) - existing:
            widget.destroy()
        logging.getLogger(__name__).warning(
            "Optional HTML renderer unavailable; using plain Tk text: %s", exc
        )
        return None


class SimpleChatUI(tk.Tk):
    def __init__(self, voice_server_url=""):
        super().__init__()

        self.title("Simple Chat + Text UI")
        self.geometry("980x620")
        self.minsize(900, 560)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        # External callback for when the user sends a message
        # The controller assigns this.
        self.on_send = None
        self.on_clear_chat = None  # External callback for when the user clears the chat
        self.on_open_history = None  # External callback for opening history
        self.on_refresh_hosts = None  # External callback for refreshing host/model lists
        self._voice_server_url = voice_server_url
        self._voice_turn = None

        # Main horizontal paned window (left/right)
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=0, column=0, sticky="nsew")

        # ===== Left: chat panel =====
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(0, weight=1)

        # Chat history (with scrollbar)
        chat_frame = ttk.Frame(left_frame)
        chat_frame.grid(row=0, column=0, sticky="nsew")
        self._chat_messages = []
        self.chat_text = None
        self.chat_html = _create_html_frame(chat_frame)
        self._use_html_chat = self.chat_html is not None

        if self._use_html_chat:
            self.chat_html.pack(fill=tk.BOTH, expand=True)
            self._chat_html_resize_job = None
            self._chat_html_last_width = 0
            self.chat_html.bind("<Configure>", self._on_chat_html_resize)
            self._install_html_copy_menu(self.chat_html, self.get_chat_transcript)
            self._render_chat_html()
        else:
            self.chat_text = tk.Text(chat_frame, wrap="word")
            self.chat_text.configure(
                bg="#111111",
                fg="#f5f5f5",
                insertbackground="#f5f5f5",
                selectbackground="#5aa6ff",
                selectforeground="#000000",
            )
            chat_scroll = ttk.Scrollbar(chat_frame, command=self.chat_text.yview)
            self.chat_text.configure(yscrollcommand=chat_scroll.set)

            # Define text tags for chat "bubbles"
            self.chat_text.tag_configure(
                "user_bubble",
                foreground="#ffffff",
                background="#007785",
                lmargin1=120,
                lmargin2=120,
                rmargin=10,
                spacing1=6,
                spacing3=6,
                justify="right",
            )
            self.chat_text.tag_configure(
                "bot_bubble",
                foreground="#ffffff",
                background="#333333",
                lmargin1=10,
                lmargin2=10,
                rmargin=120,
                spacing1=6,
                spacing3=6,
                justify="left",
            )
            self.chat_text.tag_raise("sel")

            self.chat_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)

            self._install_text_copy_menu(self.chat_text)
            self._make_text_readonly(self.chat_text)

        # Input area at bottom of chat side
        input_frame = ttk.Frame(left_frame)
        input_frame.grid(row=1, column=0, sticky="ew")

        self.entry = ttk.Entry(input_frame)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5), pady=5)
        self.entry.bind("<Return>", self.send_message)  # press Enter to send

        clear_button = ttk.Button(input_frame, text="New chat", command=self.clear_chat)
        clear_button.pack(side=tk.RIGHT, padx=(0, 5), pady=5)

        send_button = ttk.Button(input_frame, text="Send", command=self.send_message)
        send_button.pack(side=tk.RIGHT, pady=5)
        if self._voice_server_url:
            self._voice_button = ttk.Button(input_frame, text="Talk", command=self._toggle_voice)
            self._voice_button.pack(side=tk.RIGHT, padx=5, pady=5)

        # ===== Right: log panel =====
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)
        self._log_plain_text = ""
        self._log_entries = []
        self._log_auto_collapse_char_limit = 220
        self._log_auto_collapse_line_limit = 7
        self.right_text = None
        self.log_html = _create_html_frame(right_frame) if self._use_html_chat else None
        self._use_html_logs = self.log_html is not None

        if self._use_html_logs:
            self.log_html.pack(fill=tk.BOTH, expand=True)
            self._install_html_copy_menu(self.log_html, self.get_log_text)
            self._render_log_html()
        else:
            self.right_text = tk.Text(right_frame, wrap="word")
            right_scroll = ttk.Scrollbar(right_frame, command=self.right_text.yview)
            self.right_text.configure(yscrollcommand=right_scroll.set)

            self.right_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            right_scroll.pack(side=tk.RIGHT, fill=tk.Y)

            self._install_text_copy_menu(self.right_text)
            self._make_text_readonly(self.right_text)

        # Example content on the right
        self.set_log_text(
            "This is the right-hand text panel.\n"
            "You can display logs, documentation, etc. here.\n\n"
        )

        # ===== Bottom-right: host/model selectors =====
        bottom_frame = ttk.Frame(self)
        bottom_frame.grid(row=1, column=0, sticky="ew")

        # Configure a stretching column so the selector stays on the right
        bottom_frame.columnconfigure(0, weight=1)

        # History button on the bottom-left
        history_button = ttk.Button(bottom_frame, text="History...", command=self._history_clicked)
        history_button.grid(row=0, column=0, padx=(10, 5), pady=5, sticky="w")

        host_label = ttk.Label(bottom_frame, text="Host:")
        host_label.grid(row=0, column=1, padx=(0, 5), pady=5, sticky="e")

        # These are populated by the application controller.
        self.host_var = tk.StringVar()
        self.host_combo = ttk.Combobox(
            bottom_frame,
            textvariable=self.host_var,
            state="readonly",
            width=25,
        )
        self.host_combo.grid(row=0, column=2, padx=(0, 10), pady=5, sticky="e")

        model_label = ttk.Label(bottom_frame, text="Model:")
        model_label.grid(row=0, column=3, padx=(0, 5), pady=5, sticky="e")

        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(
            bottom_frame,
            textvariable=self.model_var,
            state="normal",
            width=30,
        )
        self.model_combo.grid(row=0, column=4, padx=(0, 10), pady=5, sticky="e")

        refresh_button = ttk.Button(
            bottom_frame,
            text="Refresh hosts",
            command=self._refresh_hosts_clicked,
        )
        refresh_button.grid(row=0, column=5, padx=(0, 10), pady=5, sticky="e")

        self.progress_canvas = tk.Canvas(
            bottom_frame,
            height=18,
            highlightthickness=0,
            bg="#111111",
        )
        self.progress_canvas.grid(
            row=1,
            column=0,
            columnspan=6,
            padx=10,
            pady=(0, 5),
            sticky="ew",
        )
    def get_chat_transcript(self) -> str:
        """Return the literal visible text of the chat transcript."""
        lines = []
        for msg in self._chat_messages:
            speaker = msg.get("speaker", "Bot")
            text = msg.get("text", "")
            lines.append(f"{speaker}: {text}")
        return "\n".join(lines).rstrip("\n")

    def set_chat_transcript(self, text: str) -> None:
        """Replace the entire chat transcript with the provided text."""
        self._clear_chat_display()
        if text:
            messages, saw_prefix = self._parse_transcript_to_messages(text)
            if saw_prefix:
                for speaker, msg in messages:
                    self._append_chat(speaker, msg)
            else:
                self._append_chat("Bot", text)

    def set_chat_messages(self, messages) -> None:
        """Replace the entire chat transcript with structured message data."""
        self._clear_chat_display()
        if not messages:
            return
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content") or ""
            if not content:
                continue
            if role == "user":
                self._append_chat("You", content)
            elif role == "assistant":
                self._append_chat("Bot", content)

    def get_log_text(self) -> str:
        """Return the plain-text log transcript used for persistence."""
        try:
            return self._log_plain_text
        except Exception:
            return ""

    def set_log_text(self, text: str) -> None:
        """Replace the entire contents of the log panel from plain text."""
        normalized = str(text or "")
        self._log_plain_text = normalized
        self._log_entries = []

        if normalized:
            for entry in self._split_log_entries_for_display(normalized):
                self._append_log_entry(entry, auto_collapse=True)

        self._render_log_view()

    def set_host_and_model(self, host: str, model: str) -> None:
        """Set host and model selection if they are still available in the UI."""
        try:
            if host:
                hosts = list(self.host_combo["values"])
                if host in hosts:
                    self.host_var.set(host)
        except Exception:
            pass

        try:
            if model:
                self.model_var.set(model)
        except Exception:
            pass

    def show_history_dialog(self, conversations, on_select):
        """Show a modal history dialog listing past conversations.

        `conversations` is an iterable of dicts with keys:
          - id
          - title
          - created_at
          - host
          - model
        `on_select` is a callback taking a single conversation id.
        """
        dialog = tk.Toplevel(self)
        dialog.title("Conversation History")
        dialog.transient(self)
        dialog.grab_set()

        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        columns = ("title", "created", "host", "model")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", selectmode="browse")
        tree.heading("title", text="Title")
        tree.heading("created", text="Created")
        tree.heading("host", text="Host")
        tree.heading("model", text="Model")

        tree.column("title", width=300, anchor="w")
        tree.column("created", width=160, anchor="w")
        tree.column("host", width=120, anchor="w")
        tree.column("model", width=160, anchor="w")

        vsb = ttk.Scrollbar(dialog, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        for conv in conversations:
            conv_id = conv.get("id", "")
            title = conv.get("title", "")
            created = conv.get("created_at", "") or conv.get("updated_at", "") or ""
            host = conv.get("host", "")
            model = conv.get("model", "")
            if not conv_id:
                continue
            tree.insert("", "end", iid=conv_id, values=(title, created, host, model))

        def _do_select():
            sel = tree.focus()
            if sel and on_select is not None:
                on_select(sel)
            dialog.destroy()

        def _on_cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=5, padx=10, sticky="e")

        open_btn = ttk.Button(btn_frame, text="Open", command=_do_select)
        open_btn.pack(side="left", padx=(0, 5))

        cancel_btn = ttk.Button(btn_frame, text="Cancel", command=_on_cancel)
        cancel_btn.pack(side="left")

        def _on_double_click(event):
            _do_select()

        tree.bind("<Double-1>", _on_double_click)
        dialog.protocol("WM_DELETE_WINDOW", _on_cancel)

        dialog.wait_window(dialog)
    def _history_clicked(self):
        """Internal handler for History... button."""
        if callable(self.on_open_history):
            self.on_open_history()

    def _refresh_hosts_clicked(self):
        """Internal handler for Refresh hosts button."""
        if callable(self.on_refresh_hosts):
            self.on_refresh_hosts()

    # === Public view helper methods ===

    def ask_yes_no(self, title: str, message: str) -> bool:
        """Thread-safe synchronous Yes/No dialog. Returns True if user clicks Yes."""
        result = {"value": False}
        event = threading.Event()

        def _ask():
            try:
                result["value"] = messagebox.askyesno(title, message)
            finally:
                event.set()

        # Schedule the dialog to be created on the main Tk thread
        self.after(0, _ask)

        # Block this (background) thread until user responds
        event.wait()
        return result["value"]

    def show_user_message(self, text: str) -> None:
        """Display a user chat bubble in a thread-safe way."""
        if not text:
            return

        def _do():
            self._append_chat("You", text)

        self.after(0, _do)

    def show_bot_message(self, text: str) -> None:
        """Display a bot chat bubble in a thread-safe way."""
        if not text:
            return

        def _do():
            self._append_chat("Bot", text)

        self.after(0, _do)

    def clear_chat(self):
        """Start a new chat: clear UI and reset conversation state."""
        # Clear chat display
        self._clear_chat_display()

        # Optionally clear the input field
        self.entry.delete(0, tk.END)

        # Reset the progress bar
        self.update_progress(0.0, 0.0)

        # Notify controller so it can reset conversation state
        if getattr(self, "on_clear_chat", None) is not None:
            self.on_clear_chat()

    def append_log(self, text: str) -> None:
        """Append text to the right-hand panel in a thread-safe way."""
        if text is None:
            return

        def _do():
            payload = str(text)
            display_text = payload.rstrip("\n")
            if display_text:
                # Persist each visual log entry as a blank-line separated block
                # so history restore can keep adjacent JSON payloads separate.
                self._log_plain_text += display_text + "\n\n"
                self._append_log_entry(display_text, auto_collapse=True)
            else:
                self._log_plain_text += "\n"
            self._render_log_view()

        self.after(0, _do)

    def _split_log_entries_for_display(self, text: str):
        """Heuristically split plain-text logs into display entries."""
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not normalized:
            return []

        chunks = [part.strip("\n") for part in re.split(r"\n{2,}", normalized) if part.strip("\n")]
        if len(chunks) <= 1:
            chunks = [normalized]

        result = []
        for chunk in chunks:
            lines = chunk.splitlines()
            buffered = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("Tool output for "):
                    if buffered:
                        prior = "\n".join(buffered).strip("\n")
                        if prior:
                            result.append(prior)
                    buffered = [line]
                    continue
                if (
                    stripped.startswith("Tool calls:")
                    or stripped.startswith("Agent message:")
                ):
                    if buffered:
                        prior = "\n".join(buffered).strip("\n")
                        if prior:
                            result.append(prior)
                        buffered = []
                    tool_line = stripped
                    if tool_line:
                        result.append(tool_line)
                    continue
                buffered.append(line)
            if buffered:
                tail = "\n".join(buffered).strip("\n")
                if tail:
                    result.append(tail)

        return result

    def _is_tool_calls_summary(self, text: str) -> bool:
        """Return True when the entry should use highlighted summary styling."""
        stripped = str(text or "").strip()
        if not stripped:
            return False
        first_line = stripped.splitlines()[0].strip()
        return first_line.startswith("Tool calls:") or first_line.startswith("Agent message:")

    def _looks_like_json_payload(self, text: str) -> bool:
        """Detect whether an entry likely contains JSON."""
        stripped = str(text or "").lstrip()
        return stripped.startswith("{") or stripped.startswith("[")

    def _is_tool_output_entry(self, text: str) -> bool:
        """Detect entries that start with a tool output header."""
        stripped = str(text or "").strip()
        if not stripped:
            return False
        first_line = stripped.splitlines()[0].strip()
        return first_line.startswith("Tool output for ")

    def _should_auto_collapse_log(self, text: str) -> bool:
        """Decide whether an entry should start collapsed."""
        stripped = str(text or "").strip()
        if not stripped:
            return False
        if self._is_tool_calls_summary(stripped):
            return False
        if self._is_tool_output_entry(stripped):
            return True

        line_count = stripped.count("\n") + 1
        if self._looks_like_json_payload(stripped) and line_count > 1:
            return True
        if line_count >= self._log_auto_collapse_line_limit:
            return True
        if len(stripped) >= self._log_auto_collapse_char_limit:
            return True
        return False

    def _summarize_log_entry(self, text: str) -> str:
        """Build a one-line summary for collapsed headers."""
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        if not lines:
            return "(empty log entry)"

        preview = ""
        for line in lines:
            if line in {"{", "}", "[", "]", "],", "},"}:
                continue
            preview = line.rstrip(",")
            break
        if not preview:
            preview = lines[0]

        if len(preview) > 100:
            preview = preview[:97] + "..."

        line_count = max(1, len(str(text).splitlines()))
        if self._looks_like_json_payload(text):
            return f"JSON ({line_count} lines): {preview}"
        if line_count > 1:
            return f"{preview} ({line_count} lines)"
        return preview

    def _append_log_entry(self, text: str, auto_collapse: bool = True) -> None:
        """Store one normalized log entry for display rendering."""
        content = str(text or "").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        if not content:
            return
        collapsed = auto_collapse and self._should_auto_collapse_log(content)
        self._log_entries.append({"text": content, "collapsed": bool(collapsed)})

    def _log_text_to_html(self, text: str) -> str:
        """Convert plain log text to HTML while preserving line indentation."""
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        html_lines = []
        for line in normalized.split("\n"):
            leading = line[:len(line) - len(line.lstrip(" \t"))]
            rest = line[len(leading):]

            indent_html = ""
            for ch in leading:
                if ch == "\t":
                    indent_html += "&nbsp;" * 4
                else:
                    indent_html += "&nbsp;"

            html_lines.append(indent_html + html.escape(rest))
        return "<br>".join(html_lines)

    def _set_log_html_document(self, document: str) -> None:
        """Load an HTML document into the log widget across API variants."""
        if self.log_html is None:
            return

        loaded = False
        if hasattr(self.log_html, "load_html"):
            self.log_html.load_html(document)
            loaded = True
        elif hasattr(self.log_html, "set_content"):
            self.log_html.set_content(document)
            loaded = True
        elif hasattr(self.log_html, "set_html"):
            self.log_html.set_html(document)
            loaded = True

        if not loaded:
            return

        try:
            if hasattr(self.log_html, "yview_moveto"):
                self.log_html.yview_moveto(1.0)
            elif hasattr(self.log_html, "html") and hasattr(self.log_html.html, "yview_moveto"):
                self.log_html.html.yview_moveto(1.0)
        except Exception:
            pass

    def _render_log_html(self) -> None:
        """Render log entries into a simple collapsible HTML transcript."""
        if self.log_html is None:
            return

        blocks = []
        for entry in self._log_entries:
            text = entry.get("text") or ""
            collapsed = bool(entry.get("collapsed"))
            escaped_body = self._log_text_to_html(text)

            if collapsed:
                summary = html.escape(self._summarize_log_entry(text))
                blocks.append(
                    f"""
                    <details class="log-entry">
                      <summary>{summary}</summary>
                      <div class="log-text">{escaped_body}</div>
                    </details>
                    """
                )
            else:
                classes = "log-entry tool-call" if self._is_tool_calls_summary(text) else "log-entry"
                blocks.append(
                    f"""
                    <div class="{classes}">
                      <div class="log-text">{escaped_body}</div>
                    </div>
                    """
                )

        if not blocks:
            blocks.append('<div class="empty-state">No logs yet.</div>')

        document = f"""
        <html>
          <head>
            <style>
              body {{
                margin: 0;
                padding: 12px;
                background: #111111;
                color: #f5f5f5;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                font-size: 13px;
                max-width: 100%;
                overflow-x: hidden;
              }}
              * {{
                box-sizing: border-box;
              }}
              .log-entry {{
                margin: 0 0 8px 0;
                border-radius: 8px;
                background: #1b1b1b;
                width: 100%;
                max-width: 100%;
                overflow: hidden;
              }}
              .log-text {{
                display: block;
                width: 100%;
                max-width: 100%;
                margin: 0;
                padding: 8px 10px;
                white-space: normal;
                word-break: break-word;
                word-wrap: break-word;
                overflow-wrap: anywhere;
                font-family: "SFMono-Regular", Menlo, Consolas, monospace;
                font-size: 12px;
                line-height: 1.35;
              }}
              .log-entry summary {{
                display: block;
                width: 100%;
                max-width: 100%;
                cursor: default;
                padding: 8px 10px;
                list-style-position: inside;
                color: #d8dde7;
                font-size: 12px;
                white-space: normal;
                word-break: break-word;
                overflow-wrap: anywhere;
              }}
              details.log-entry > summary::-webkit-details-marker {{
                display: inline;
              }}
              details.log-entry[open] summary {{
                margin-bottom: 2px;
              }}
              .tool-call {{
                background: #112234;
              }}
              .tool-call .log-text {{
                color: #d7ecff;
                font-weight: 600;
              }}
              .empty-state {{
                color: #9fa3ad;
                font-size: 12px;
              }}
            </style>
          </head>
          <body>
            {''.join(blocks)}
          </body>
        </html>
        """

        self._set_log_html_document(document)

    def _render_log_view(self) -> None:
        """Render the current log state in either HTML or text mode."""
        if self._use_html_logs:
            self._render_log_html()
            return
        if self.right_text is None:
            return
        self.right_text.delete("1.0", tk.END)
        if self._log_plain_text:
            self.right_text.insert("1.0", self._log_plain_text)
        self.right_text.see(tk.END)

    def update_progress(self, prompt_ratio, total_ratio):
        """Update the bottom progress bar with context usage numbers.

        prompt_ratio: float between 0 and 1 for prompt-only context usage.
        total_ratio:  float between 0 and 1 for prompt+completion usage.
        """
        def _update():
            # Clamp to [0, 1]
            pr = max(0.0, min(float(prompt_ratio or 0.0), 1.0))
            tr = max(0.0, min(float(total_ratio or 0.0), 1.0))

            if not hasattr(self, "progress_canvas"):
                return

            canvas = self.progress_canvas
            canvas.delete("all")

            # Determine current canvas size
            width = canvas.winfo_width() or canvas.winfo_reqwidth() or 1
            height = canvas.winfo_height() or canvas.winfo_reqheight() or 1

            # Background
            canvas.create_rectangle(
                0, 0, width, height,
                fill="#222222",
                outline="",
            )

            # Total (prompt + output) bar in one color (back layer)
            total_width = int(width * tr)
            if total_width > 0:
                canvas.create_rectangle(
                    0, 0, total_width, height,
                    fill="#4455aa",
                    outline="",
                )

            # Prompt-only bar in a different color (front layer)
            prompt_width = int(width * pr)
            if prompt_width > 0:
                canvas.create_rectangle(
                    0, 0, prompt_width, height,
                    fill="#55aa55",
                    outline="",
                )

            # Text overlay with percentages
            prompt_pct = int(pr * 100)
            total_pct = int(tr * 100)
            label = f"Context: {prompt_pct}% \u00b7 With output: {total_pct}%"
            canvas.create_text(
                width // 2,
                height // 2,
                text=label,
                fill="#ffffff",
                font=("TkDefaultFont", 9),
            )

        # Ensure UI updates happen on the main thread
        self.after(0, _update)

    # === Internal helpers ===

    def _clear_chat_display(self) -> None:
        """Clear all chat messages and reset the chat panel."""
        self._cancel_voice()
        self._chat_messages = []
        if self._use_html_chat:
            self._render_chat_html()
            return
        if self.chat_text is not None:
            self.chat_text.delete("1.0", tk.END)

    def _markdown_to_html(self, text: str) -> str:
        """Convert markdown to HTML and fall back to escaped plain text on failure."""
        if markdown is None:
            return html.escape(text).replace("\n", "<br>")
        try:
            return markdown.markdown(
                text,
                extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
            )
        except Exception:
            return html.escape(text).replace("\n", "<br>")

    def _normalize_display_markdown_text(self, text: str) -> str:
        """Normalize non-breaking spaces that can force wide table columns."""
        if not text:
            return text
        return text.replace("\u00a0", " ").replace("\u202f", " ")

    def _add_inline_style(self, html_text: str, tag_name: str, style_text: str) -> str:
        """Add style attributes directly to tags for more reliable tkinterweb rendering."""
        pattern = re.compile(rf"<{tag_name}(\s[^>]*)?>", re.IGNORECASE)

        def _repl(match):
            attrs = match.group(1) or ""
            style_match = re.search(r'style\s*=\s*"([^"]*)"', attrs, flags=re.IGNORECASE)
            if style_match:
                current = style_match.group(1).strip()
                merged = f"{current}; {style_text}" if current else style_text
                new_attrs = re.sub(
                    r'style\s*=\s*"([^"]*)"',
                    f'style="{merged}"',
                    attrs,
                    flags=re.IGNORECASE,
                )
            else:
                new_attrs = f'{attrs} style="{style_text}"'
            return f"<{tag_name}{new_attrs}>"

        return pattern.sub(_repl, html_text)

    def _normalize_rendered_html(self, rendered_html: str) -> str:
        """Normalize markdown HTML so wide tables wrap inside bubbles."""
        normalized = rendered_html
        normalized = self._add_inline_style(
            normalized,
            "table",
            "width:100%; max-width:100%; table-layout:fixed; border-collapse:collapse;",
        )
        normalized = self._add_inline_style(
            normalized,
            "th",
            "word-break:break-word; white-space:normal; overflow-wrap:anywhere;",
        )
        normalized = self._add_inline_style(
            normalized,
            "td",
            "word-break:break-word; white-space:normal; overflow-wrap:anywhere;",
        )
        normalized = self._add_inline_style(
            normalized,
            "pre",
            "width:100%; max-width:100%; overflow-x:auto;",
        )
        normalized = self._add_inline_style(
            normalized,
            "code",
            "word-break:break-word; white-space:pre-wrap;",
        )
        return normalized

    def _set_chat_html_document(self, document: str) -> None:
        """Load an HTML document into the chat widget across API variants."""
        if self.chat_html is None:
            return

        loaded = False
        if hasattr(self.chat_html, "load_html"):
            self.chat_html.load_html(document)
            loaded = True
        elif hasattr(self.chat_html, "set_content"):
            self.chat_html.set_content(document)
            loaded = True
        elif hasattr(self.chat_html, "set_html"):
            self.chat_html.set_html(document)
            loaded = True

        if not loaded:
            return

        try:
            if hasattr(self.chat_html, "yview_moveto"):
                self.chat_html.yview_moveto(1.0)
            elif hasattr(self.chat_html, "html") and hasattr(self.chat_html.html, "yview_moveto"):
                self.chat_html.html.yview_moveto(1.0)
        except Exception:
            pass

    def _on_chat_html_resize(self, event=None) -> None:
        """Re-render chat after width changes so bubble layout stays stable."""
        if not self._use_html_chat:
            return
        if event is not None:
            width = int(getattr(event, "width", 0) or 0)
            if width <= 0 or abs(width - self._chat_html_last_width) < 8:
                return
            self._chat_html_last_width = width

        if getattr(self, "_chat_html_resize_job", None) is not None:
            try:
                self.after_cancel(self._chat_html_resize_job)
            except Exception:
                pass
        self._chat_html_resize_job = self.after(80, self._render_chat_html)

    def _render_chat_html(self) -> None:
        """Render the entire chat transcript as markdown-aware HTML bubbles."""
        if self.chat_html is None:
            return
        self._chat_html_resize_job = None

        bubble_html = []
        for msg in self._chat_messages:
            speaker = msg.get("speaker", "Bot")
            text = msg.get("text", "")
            role_class = "user" if speaker == "You" else "bot"
            display_text = self._normalize_display_markdown_text(text)
            rendered = self._markdown_to_html(display_text)
            rendered = self._normalize_rendered_html(rendered)
            bubble_html.append(
                f"""
                <div class="row {role_class}">
                  <div class="bubble {role_class}">
                    <div class="meta">{html.escape(speaker)}</div>
                    <div class="body">{rendered}</div>
                  </div>
                </div>
                """
            )

        if not bubble_html:
            bubble_html.append('<div class="empty-state">Start a conversation...</div>')

        document = f"""
        <html>
          <head>
            <style>
              body {{
                margin: 0;
                padding: 14px;
                background: #111111;
                color: #f5f5f5;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                font-size: 15px;
              }}
              .row {{
                display: block;
                width: 100%;
                margin: 0 0 10px 0;
                clear: both;
              }}
              .bubble {{
                display: block;
                padding: 12px 14px;
                border-radius: 20px;
                line-height: 1.35;
                text-align: left;
                word-break: break-word;
                overflow: hidden;
              }}
              .row.user .bubble {{
                margin-left: 28%;
                margin-right: 0;
              }}
              .row.bot .bubble {{
                margin-right: 28%;
                margin-left: 0;
              }}
              .bubble.user {{
                background: #007785;
                border-bottom-right-radius: 8px;
              }}
              .bubble.bot {{
                background: #2f3238;
                border-bottom-left-radius: 8px;
              }}
              .meta {{
                font-size: 12px;
                opacity: 0.75;
                margin-bottom: 6px;
              }}
              .body {{
                font-size: 15px;
              }}
              .body p {{
                margin: 0 0 8px 0;
              }}
              .body p:last-child {{
                margin-bottom: 0;
              }}
              .body code {{
                background: rgba(0, 0, 0, 0.28);
                border-radius: 4px;
                padding: 2px 4px;
                font-family: "SFMono-Regular", Menlo, Consolas, monospace;
              }}
              .body pre {{
                background: rgba(0, 0, 0, 0.35);
                border-radius: 8px;
                padding: 10px;
                overflow-x: auto;
              }}
              .body pre code {{
                background: transparent;
                padding: 0;
              }}
              .body a {{
                color: #8cc9ff;
              }}
              .body table {{
                border-collapse: collapse;
                width: 100%;
                table-layout: fixed;
                font-size: 14px;
              }}
              .body th, .body td {{
                border: 1px solid rgba(255, 255, 255, 0.2);
                padding: 5px 6px;
                word-break: break-word;
              }}
              .empty-state {{
                color: #9fa3ad;
                font-size: 13px;
              }}
            </style>
          </head>
          <body>
            {''.join(bubble_html)}
          </body>
        </html>
        """
        self._set_chat_html_document(document)

    def _append_chat(self, speaker, text):
        """Low-level chat line append with bubble-style formatting."""
        if not text:
            return

        self._chat_messages.append({"speaker": speaker, "text": text})

        if self._use_html_chat:
            self._render_chat_html()
            return

        tag = "user_bubble" if speaker == "You" else "bot_bubble"
        if self.chat_text is not None:
            self.chat_text.insert(tk.END, f"{speaker}: {text}\n", (tag,))
            self.chat_text.see(tk.END)

    def _parse_transcript_to_messages(self, text: str):
        """Parse transcript text into message tuples: (speaker, content)."""
        lines = text.splitlines()
        if not lines:
            return [], False

        messages = []
        current_speaker = None
        current_lines = []
        saw_prefix = False

        def _flush():
            nonlocal current_speaker, current_lines
            if current_speaker is None:
                return
            msg = "\n".join(current_lines).rstrip("\n")
            messages.append((current_speaker, msg))
            current_speaker = None
            current_lines = []

        for line in lines:
            if line.startswith("You:"):
                saw_prefix = True
                _flush()
                current_speaker = "You"
                current_lines = [line[len("You:"):].lstrip()]
            elif line.startswith("Bot:"):
                saw_prefix = True
                _flush()
                current_speaker = "Bot"
                current_lines = [line[len("Bot:"):].lstrip()]
            else:
                if current_speaker is None:
                    current_lines = [line]
                    current_speaker = "Bot"
                else:
                    current_lines.append(line)

        _flush()

        return messages, saw_prefix

    # === Event handlers ===

    def _toggle_voice(self):
        if self._voice_turn is not None:
            self._voice_turn.stopped.set()
            self._voice_button.configure(text="Transcribing…", state="disabled")
            return

        from tk_voice import VoiceTurn

        self._voice_turn = VoiceTurn(self._voice_server_url)
        self._voice_button.configure(text="Stop recording")
        self.after(50, self._poll_voice, self._voice_turn)

    def _poll_voice(self, turn):
        if turn is not self._voice_turn:
            return
        try:
            transcript, error = turn.result.get_nowait()
        except Empty:
            if turn.stopped.is_set():
                self._voice_button.configure(text="Transcribing…", state="disabled")
            self.after(50, self._poll_voice, turn)
            return

        self._voice_turn = None
        self._voice_button.configure(text="Talk", state="normal")
        if error:
            messagebox.showerror("Voice input", error, parent=self)
        elif transcript:
            self._submit_message(transcript)
        else:
            self._show_toast("No speech detected")

    def _cancel_voice(self):
        if self._voice_turn is not None:
            self._voice_turn.cancel()
            self._voice_turn = None
            self._voice_button.configure(text="Talk", state="normal")

    def destroy(self):
        self._cancel_voice()
        super().destroy()

    def send_message(self, event=None):
        """Handle user sending a message from the entry field.

        This method is UI-only: it reads the text, shows it in the chat,
        clears the entry, and notifies the controller via `on_send`.
        """
        msg = self.entry.get().strip()
        if not msg:
            return

        self.entry.delete(0, tk.END)
        self._submit_message(msg)

    def _submit_message(self, msg):
        self.show_user_message(msg)
        if self.on_send is not None:
            self.on_send(msg)

    def _install_text_copy_menu(self, widget: tk.Text) -> None:
        """Attach a right-click copy menu and shortcuts to a Text widget."""
        menu = tk.Menu(widget, tearoff=0)

        def _copy_selection():
            try:
                text = widget.get("sel.first", "sel.last")
            except tk.TclError:
                return
            widget.clipboard_clear()
            widget.clipboard_append(text)
            self._show_toast("Text copied")

        def _select_all():
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "1.0")
            widget.see("insert")

        menu.add_command(label="Copy", command=_copy_selection)
        menu.add_command(label="Select All", command=_select_all)

        def _popup(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        widget.bind("<Button-3>", _popup)  # Windows/Linux
        widget.bind("<Button-2>", _popup)  # Some Linux configs
        widget.bind("<Control-Button-1>", _popup)  # macOS

        def _copy_shortcut(event):
            _copy_selection()
            return "break"

        def _select_all_shortcut(event):
            _select_all()
            return "break"

        widget.bind("<Control-c>", _copy_shortcut)
        widget.bind("<Command-c>", _copy_shortcut)
        widget.bind("<Control-a>", _select_all_shortcut)
        widget.bind("<Command-a>", _select_all_shortcut)

    def _install_html_copy_menu(self, widget, copy_all_provider=None) -> None:
        """Attach copy helpers for HTML-rendered widgets."""
        menu = tk.Menu(widget, tearoff=0)

        def _copy_to_clipboard(text: str) -> bool:
            if not text:
                return False
            try:
                widget.clipboard_clear()
                widget.clipboard_append(text)
                self._show_toast("Text copied")
                return True
            except Exception:
                return False

        def _selection_text() -> str:
            # tkinterweb selection support varies across versions, so try several
            # APIs and fall back to the generic Tk selection owner.
            for owner in (widget, getattr(widget, "html", None), self):
                if owner is None:
                    continue
                try:
                    text = owner.selection_get()
                except Exception:
                    continue
                if text:
                    return text
            try:
                text = widget.tk.call("selection", "get", "-displayof", widget)
                return text or ""
            except Exception:
                return ""

        def _copy_selection() -> bool:
            return _copy_to_clipboard(_selection_text())

        def _copy_all() -> bool:
            if copy_all_provider is None:
                return False
            try:
                payload = copy_all_provider() or ""
            except Exception:
                payload = ""
            return _copy_to_clipboard(payload)

        menu.add_command(label="Copy", command=_copy_selection)
        if copy_all_provider is not None:
            menu.add_command(label="Copy All", command=_copy_all)

        def _event_from_widget(event) -> bool:
            try:
                widget_path = str(widget)
                event_path = str(getattr(event, "widget", ""))
                return event_path.startswith(widget_path)
            except Exception:
                return False

        def _popup(event):
            if not _event_from_widget(event):
                return
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        def _copy_shortcut(event):
            if not _event_from_widget(event):
                return None
            if _copy_selection():
                return "break"
            return None

        widget.bind_all("<Button-3>", _popup, add="+")   # Windows/Linux
        widget.bind_all("<Button-2>", _popup, add="+")   # Some Linux configs
        widget.bind_all("<Control-Button-1>", _popup, add="+")  # macOS
        widget.bind_all("<Control-c>", _copy_shortcut, add="+")
        widget.bind_all("<Command-c>", _copy_shortcut, add="+")

    def _show_toast(self, message: str) -> None:
        """Show a short-lived toast near the bottom-right of the window."""
        try:
            toast = tk.Toplevel(self)
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)
            label = tk.Label(
                toast,
                text=message,
                bg="#222222",
                fg="#ffffff",
                padx=10,
                pady=6,
            )
            label.pack()

            self.update_idletasks()
            x = self.winfo_rootx() + self.winfo_width() - toast.winfo_reqwidth() - 20
            y = self.winfo_rooty() + self.winfo_height() - toast.winfo_reqheight() - 20
            toast.geometry(f"+{max(x, 0)}+{max(y, 0)}")

            toast.after(1000, toast.destroy)
        except Exception:
            # Toast is optional; ignore failures.
            pass

    def _make_text_readonly(self, widget: tk.Text) -> None:
        """Prevent edits while allowing selection, scrolling, and copy."""
        def _block_edit(event):
            return "break"

        for sequence in (
            "<Key>",
            "<BackSpace>",
            "<Delete>",
            "<<Paste>>",
            "<<Cut>>",
        ):
            widget.bind(sequence, _block_edit)
