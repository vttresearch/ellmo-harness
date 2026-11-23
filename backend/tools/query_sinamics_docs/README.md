# Siemens SINAMICS V20 document setup

Download the operating instructions from the
[official Siemens page](https://support.industry.siemens.com/cs/ww/en/view/109811111)
and place the PDF in this directory as
`Siemens SINAMICS V20_op_instr_0522_en-US.pdf`.

To display the same manual in Unity, copy this local PDF to
`examples/unity-connector/Assets/StreamingAssets/` from the repository root,
keeping its filename and edition, then restart the scene. Create the folder if
needed. Both Unity scenes can run without it and show **PDF unavailable** with
setup guidance. The PDF is optional for the viewer but needed for SINAMICS RAG queries.

Search results use the PDF's printed page labels: `page=411 pdf_page=413` means
printed page 411, at physical position 413. Pass `page=411` to `change_page` unchanged.
Roman labels are supported too. The index continues to store physical positions;
existing indexes do not need rebuilding for this numbering change.

The PDF and generated `cache/` directory are ignored and must not be committed. Run
`python tools/query_sinamics_docs/diagnose.py` from `backend/` to check local setup.
