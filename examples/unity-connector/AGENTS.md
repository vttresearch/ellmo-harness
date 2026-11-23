# Unity connector guide

Read the repository-root `AGENTS.md` and `docs/setup.md`. This directory is the
complete example Unity project; use the Editor version in
`ProjectSettings/ProjectVersion.txt`.

- Preserve `.meta` files, asset GUIDs, package pins and project settings.
- Keep desktop and OpenXR scenes aligned for shared features; prefer OpenXR for XR.
- Keep tool inputs and returned evidence separately inspectable and expandable.
- Match the backend's documented REST/WebSocket contracts and correlation IDs.
- Keep optional speech controls disabled unless the user supplies a speech service.
- Do not commit Library, generated IDE/build files, user settings, documents or logs.
- Keep user PDFs and their generated caches out of public source.
- Report Editor compilation and manual/hardware checks accurately. Do not claim XR
  validation from desktop or batch compilation alone.

Do not commit or publish unless requested.
