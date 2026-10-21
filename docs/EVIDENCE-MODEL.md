# Evidence model

The manifest separates observed file properties from operator-supplied context.

- SHA-256, byte size, format, width, and height are measured from the packaged file.
- URL, title, browser, capture time, and notes are assertions entered by the operator.
- Verification detects later file changes but cannot establish when or how a screenshot was captured.
- Files are copied byte-for-byte; the HTML index references those copies and is not itself evidence of website state.


## Verification boundaries

The screenshot manifest is the authority for listed screenshots, not a signature.
The HTML index is a presentation file and is not itself authenticated by the manifest.
Files absent from the manifest are not authenticated evidence. All recorded context
remains an operator assertion, including apparently valid URLs and timestamps.
Strict manifest typing prevents ambiguous measurements; it cannot establish provenance.

Image validation checks the supported format, dimensions and first-frame decoding.
All supplied bytes, including animation data, are copied and hashed; later animation
frames are not individually certified. Keep files stable during verification.
