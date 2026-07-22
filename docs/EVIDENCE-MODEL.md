# Evidence model

The manifest separates observed file properties from operator-supplied context.

- SHA-256, byte size, format, width, and height are measured from the packaged file.
- URL, title, browser, capture time, and notes are assertions entered by the operator.
- Verification detects later file changes but cannot establish when or how a screenshot was captured.
- Files are copied byte-for-byte; the HTML index references those copies and is not itself evidence of website state.
