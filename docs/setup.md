# Setup and configuration

Use Python 3.13. Create `backend/config.json` from `backend/config.example.json`; the
real file is ignored. API keys belong in `OPENAI_API_KEY` or `OPENROUTER_API_KEY`, not
in Git. Model and package caches are downloaded locally and ignored.

When updating an existing installation, align local tool `name`, `module` and
`function` entries with the example configuration while preserving your connection
settings and enabled flags. The Unity color command is `change_gameobject_color`;
update and restart both the backend and Unity client together.

For API-key commands, model selection, Ollama or llama.cpp setup, and remote Ollama access,
follow the [model backend setup in the README](../README.md#model-backends).

The project environment lives in `.venv`. Use a base Python installation outside
the checkout; if using uv, keep its default Python installation directory.
The environment and caches are local setup state, excluded from Git.

## Python dependencies

For Python 3.13 on macOS arm64, install the tested package versions:

```sh
python -m pip install --require-hashes -r requirements-macos-arm64.lock
```

pip verifies the downloads automatically; no manual checksum comparison is needed.
The lock was installed and checked in a fresh environment on macOS 26.7 arm64.
It covers Python packages only; runtime model downloads and Unity/hardware need
separate validation. See [the dependency inventory](python-dependencies.md).
The general `requirements.txt` path pins direct dependencies and allows their
transitive dependencies to resolve; use this path on Windows.

<a id="tk-desktop-client"></a>

## Desktop chat app

A Python desktop chat app is included in this repository at `backend/main.py`.
It opens a normal window where you can select a model, send test messages, and
inspect tool evidence. It uses the conversation engine directly, so you can check
your model setup without installing Unity or starting the REST/WebSocket services.

From the repository root, with the project Python environment activated and its
dependencies installed, create the app's local configuration once:

```sh
cp backend/config.tk.example.json backend/config.tk.json
```

Preserve this file on later launches. Configure its `model_hosts` entries for a
running Ollama or llama.cpp server with a model available, or a hosted provider
with its API key in an environment variable. Then open the app:

```sh
ELLMO_CONFIG="$PWD/backend/config.tk.json" python backend/main.py
```

<details>
<summary>Windows PowerShell commands</summary>

Create the configuration only on first setup, then configure your model as above:

```powershell
Copy-Item backend/config.tk.example.json backend/config.tk.json
```

Launch with the Python environment activated:

```powershell
$env:ELLMO_CONFIG = (Resolve-Path backend/config.tk.json).Path
python backend/main.py
```

</details>

In the window that opens, select your host and model and send a short message.
A reply confirms that the app can reach your model and complete a chat turn.
Cloud model requests may incur the provider's charges. Saved chats and tool
evidence use the same local `backend/conversations/` store.

On subsequent launches, repeat only the environment-variable assignment and Python
command. Clear `ELLMO_CONFIG` when switching back to the default Unity configuration
(`unset ELLMO_CONFIG` in a shell or `Remove-Item Env:ELLMO_CONFIG` in PowerShell).

All tools are disabled in the desktop app's example configuration. You may enable
standalone tools, such as document retrieval, after supplying their local documents.
Unity colour, camera and
PDF-viewer actions need a Unity connection in the WebSocket process and cannot be
used through the desktop app. Live MQTT subscription is also started by
the WebSocket service, not by the desktop app. Use the Unity path for those
integrations.

For microphone input, see [Talk in the standalone Tk app](#talk-in-the-standalone-tk-app).

<details>
<summary>Optional message formatting</summary>

The app works with plain text by default. For richer HTML/Markdown message
formatting, install the optional packages in the same Python environment:

```sh
python -m pip install -r requirements-tk.txt
```

If these packages are missing or their native renderer cannot load, the app falls
back to plain text. The pinned Tkhtml build does not load with the Tk 9 runtime on
the tested Mac; plain text is the supported path there.

</details>

<details>
<summary>Troubleshooting: the desktop window will not open</summary>

The app uses Python's built-in windowing interface, `tkinter` (Tk). Check that it is
available by running `python -m tkinter`; a small test window should open. If you
see a missing `_tkinter` error, use a Python distribution with Tk support or install
the matching Tk package supplied by your Python/OS distributor. The optional
message-formatting packages above do not replace this requirement.

</details>

## REST/WebSocket and Unity

Run both backend services with `python run.py`. Use `--host 0.0.0.0` only when a remote
Unity client needs access, and protect the ports with an appropriate trusted network or
firewall. The APIs do not provide production authentication.
Use them only with trusted clients: the tool-management API can change which Python
functions are loaded and executed. Disabled integrations are defaults, not access controls.

The backend accepts **one connected client at a time**, with one WebSocket worker.
A second connection receives `CLIENT_ALREADY_CONNECTED` and closes with code 1008.
Run the supplied launcher without extra workers or replicas. Multiple simultaneous
clients are a planned feature; they need separate session and tool routing.

On disconnect, an in-flight model request or tool may finish, but the turn does not
start another tool or model request. The client slot stays reserved until that work
finishes and its history is saved. A model request can hold reconnection until its
provider timeout (60 seconds by default); an active tool uses its own timeout.
Already-started external actions cannot be undone.

Completed, failed and disconnected turns save messages, tool evidence and transcripts
under `backend/conversations/`. These are local, unencrypted JSON files; keep them
private. REST conversation endpoints read those snapshots. A new WebSocket connection
starts a fresh conversation; automatic chat resumption is not implemented.

The Unity project is `examples/unity-connector` and targets Unity 6000.3.11f1. Desktop
is the basic path; OpenXR requires a compatible runtime and headset.

## Optional integrations

- MQTT is disabled by default. Configure a broker and topic, then set `enabled` to
  `true`. Credentials are read from `MQTT_USERNAME` and `MQTT_PASSWORD`.
- Camera analysis is disabled by default. Set `ELLMO_VISION_HOST` and
  `ELLMO_VISION_MODEL` before enabling `get_user_pov`. Captured images are sent to that
  configured model server.
- Document tools build ignored local caches. Do not commit source documents or caches.
- For SINAMICS V20, download the document from the
  [official Siemens page](https://support.industry.siemens.com/cs/ww/en/view/109811111)
  and place it locally as described in `backend/tools/query_sinamics_docs/README.md`.

## MQTT telemetry

`get_mqtt_telemetry` summarizes a rolling window of numeric JSON readings from the
configured MQTT topic. MQTT stays disabled until explicitly enabled. The example
`telemetry.fields` block accepts `temperature`, `pressure`, and `rpm`;
replace it with your stream's field names. Extra payload fields are ignored when
an allowlist is configured. An empty `fields` object accepts all flat numeric
fields; nested data, booleans and non-finite values are rejected. `receivedAt` is
reserved for the backend receipt time. Values are reported in source units;
no unit conversion is performed.

Field aliases are case-insensitive. To enable optional rules, add `minimum`,
`maximum`, or `max_change` to a field's settings. For example, this illustrative
configuration checks a temperature sensor (choose limits appropriate to yours):

```json
"telemetry": {
  "fields": {
    "temperature": {
      "aliases": ["TEMP"],
      "unit": "degC",
      "maximum": 80,
      "max_change": 5
    }
  }
}
```

`maximum` and `minimum` inspect all received values in the requested window.
`max_change` checks the absolute difference between consecutive received values
of that field, so its interpretation depends on the sampling frequency and missing
readings. It is not a rate per second. No diagnostic thresholds are enabled in the
example configuration. A report with no applicable rules says
`diagnostics_status: "not_configured"`; it does not imply that the equipment is healthy.
Restart the backend after changing the field allowlist.

## Local document search

`query_local_document` searches one PDF, Markdown or text document. Put it in
`backend/tools/query_local_document/`, or set `ELLMO_DOCUMENT_PATH` to its local
path. Document paths are selected by the operator, not by model tool arguments.
See [document setup](../backend/tools/query_local_document/README.md).

## Unity documents

No Siemens manual is included. The desktop and OpenXR scenes request
`Siemens SINAMICS V20_op_instr_0522_en-US.pdf`; without a local copy, the viewer
shows **PDF unavailable** with a setup hint.

If you already use the manual for RAG queries, copy
`backend/tools/query_sinamics_docs/Siemens SINAMICS V20_op_instr_0522_en-US.pdf`
to `examples/unity-connector/Assets/StreamingAssets/`, creating the folder if
necessary. Otherwise, download it from the
[official Siemens page](https://support.industry.siemens.com/cs/ww/en/view/109811111)
and place a copy in each location. Keep the same filename and edition so backend
page references agree with the viewer, then restart the Unity scene.

Retrieval citations and `change_page` use **printed page labels**, read from the
PDF's page-label metadata. Pass the citation's `page=` value unchanged, including
Roman labels such as `iii`; do not use its `pdf_page=` file position. For this
Siemens edition, printed page 411 is PDF position 413. Unity acknowledges the label
and physical position only after rendering succeeds. Unknown or duplicate labels
are rejected without changing the page. PDFs without page-label metadata fall back
to one-based file positions, which may differ from their printed footers.

Leaving the PDF absent is supported. Keep local PDFs and generated document caches
out of Git. This project does not grant redistribution rights to the Siemens manual.
Backend-to-viewer document delivery is a future improvement; the current viewer
reads its own local copy from `Assets/StreamingAssets`.

The default scene list enables `Ellmo Tools Connector.unity` for desktop.
For OpenXR, enable `Ellmo Tools Connector OpenXR.unity` instead in Build Profiles.

## Optional speech

You can implement your own speech service using
[Kokoro](https://github.com/hexgrad/kokoro) or another text-to-speech engine.
No speech server or speech dependencies are distributed in this release, and
installing an engine alone does not implement the client's HTTP interface.

The existing [Unity VoiceManager](../examples/unity-connector/Assets/Scripts/VoiceManager.cs)
expects these endpoints:

- `POST /generate-audio`: JSON body `{"text": "Hello"}`; response is raw mono
  32-bit floating-point audio, with no WAV header, at `ttsSampleRate` (24,000 Hz
  by default). Match the client's native byte order when serializing samples.
- `POST /generate-text`, if microphone input is wanted: multipart field `audio`
  containing a 16,000 Hz mono WAV file; response is the plain-text transcript.
  Kokoro provides synthesis only; transcription requires a separate engine.

The service address defaults to `http://127.0.0.1:5050`. `followEllmoServerHost`
normally derives its host from the chat server and uses `voiceServerPort`; disable
it to keep a separately configured `voiceServerAddress`. Speech controls are off
by default (`ChatExample.enableVoiceControls` and `OpenXRHandUI.enableVoiceGesture`).
Enable the relevant controls only after supplying and validating your service.
These interface notes describe the client; no user-supplied speech service has
been validated as part of this release.

Keep the engine and downloaded models local. Review the licenses of the engine,
model/voice files and transitive dependencies for your intended use and distribution;
they may differ from this project's Apache-2.0 license. Automatic model downloads
do not remove those obligations.

### Talk in the standalone Tk app

Install the optional microphone dependency in the Tk app's environment with
`python -m pip install sounddevice==0.5.3`. The
[sounddevice package](https://pypi.org/project/sounddevice/0.5.3/) uses the MIT
license and wraps [PortAudio](https://www.portaudio.com/license.html), which has
its own permissive license. The macOS and Windows wheels include PortAudio.
On Linux, install your distribution's PortAudio runtime if needed.

In the local config used by `backend/main.py`, set `voice_input.enabled` to
`true` and set `voice_input.server_url` to the speech service URL used by Unity,
including its port. Use `http://127.0.0.1:5050` if the service runs on the same
machine at the default port. The service must provide `POST /generate-text`.
Allow microphone access for the app that launches Python if the OS prompts.

Click **Talk**, speak, then click **Stop recording**. The app sends the audio
to the configured service and submits the returned transcript as a chat message.
Recording stops automatically after 60 seconds.

If you run `python run.py` to start the backend, you can also set
`voice_input.launch_command` in your trusted local config. Supply a JSON array
with the absolute path to the speech service executable followed by its arguments.
The launcher starts that service alongside REST and WebSocket and stops it on exit.
Leave this setting out when the Unity speech service is already managed separately.
