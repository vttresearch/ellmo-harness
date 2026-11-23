# Third-party notices

Project code is licensed under Apache-2.0. The following retained assets and fetched
dependencies remain under their respective terms.

- Bundled TextMesh Pro essential resources: Unity Companion License. These assets
  are provided only in connection with the Unity project. The installed uGUI
  package's upstream notice is retained in [licenses/Unity-uGUI-NOTICE.md](licenses/Unity-uGUI-NOTICE.md).
  TextMesh Pro is copyright Unity Technologies ApS; see its
  [upstream notice](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/license/LICENSE.html)
  and the [Unity Companion License](https://unity.com/legal/licenses/unity-companion-license).
- Liberation Sans: SIL Open Font License; the license text is retained beside the font
  under `examples/unity-connector/Assets/TextMesh Pro/Fonts/`.
- PDFtoImage 5.2.0: MIT. It is fetched by Unity Package Manager from a pinned upstream
  commit. Its upstream notices identify SkiaSharp (MIT) and PDFium (Apache-2.0).
- Other Unity Package Manager and Python dependencies are fetched separately and retain
  the licenses and notices supplied by their publishers.
- Optional Tk rendering uses tkinterweb 4.19.2 and tkinterweb-tkhtml 2.1.1 (MIT),
  and Markdown 3.10.2 (BSD-3-Clause), as declared in their installed package metadata.
  These packages are fetched separately through `requirements-tk.txt`. Tk itself is
  supplied by the user's Python/OS distribution and retains its own notices.

The [Python dependency inventory](docs/python-dependencies.md) records all 125
distributions in the validated macOS arm64 environment, including license
metadata. It is specific to that lock and platform. Runtime model weights are
downloaded separately and are not included in this source distribution; their
licenses and revisions also need review for any packaged release or deployment.

See the dependency manifests for exact versions. This file must be regenerated and
verified whenever bundled assets or dependencies change.
