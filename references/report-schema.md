# PDF report schema

Create UTF-8 JSON in the user's language. English interface labels are the default; translate them through `labels` when needed.

## Fields
- `title` (required); optional `subtitle`, `date`, `language`, `labels`, `scope`.
- `summary` (required, concise but length and format depend on the task).
- `findings` (optional titled claims).
- `sections` (required); optional `methodology`, `limitations`, `sources`.
- `contents` (optional boolean; automatic for sufficiently long reports).

## Claims
Use a claim object when evidence type matters:
```json
{"text":"Claim [S1]","basis":"fact","sources":["S1"]}
```
`basis` is optional metadata: `fact`, `calculation`, `inference`, or `hypothesis`. Do not classify every paragraph. Use it for material, uncertain, calculated, or decision-critical claims. Facts and evidence-based inferences need sources; calculations need traceable data origins; hypotheses must be clearly phrased as unverified. Never display `basis` labels in the PDF.

## Blocks
```json
{"type":"paragraph","text":"...","basis":"fact","sources":["S1"]}
{"type":"bullets","items":["Plain text",{"text":"Claim [S2]","basis":"inference","sources":["S2"]}]}
{"type":"table","headers":["Option","Evidence"],"rows":[["A","... [S1]"]],"sources":["S1"],"col_widths":[2,1]}
{"type":"subheading","text":"..."}
{"type":"callout","title":"Recommendation","text":"...","basis":"inference","sources":["S1"]}
```
`basis` and `sources` are optional where not relevant. Cite material facts near the text. Tables support 1–5 columns; `col_widths` is optional relative width. Cite factual cells inline and list those IDs in the table's `sources`.

## Sources
```json
{"id":"S1","title":"Source title","publisher":"Author or organization","published":"2026-01-31","accessed":"2026-11-24","url":"https://example.org/source"}
```
For user files, omit `url` and describe the file and origin. Include only sources used in the report. The renderer makes source titles clickable; do not repeat raw URLs.

## Structure
Put the answer first. Keep the summary brief, but adapt its length and format to the task. Avoid repetition. For many options, summarize the full set, compare a shortlist, and detail only the strongest few. Preserve the requested scope; use appendices for supporting detail when useful.

## Labels
Custom keys: `kicker`, `date`, `scope`, `summary`, `findings`, `methodology`, `limitations`, `sources`, `published`, `accessed`, `page`, `contents`.

## Validation
Check structure, field types, source IDs, links, and table dimensions. Remove unused sources. Structural checks cannot confirm that a source supports a claim; review that match during research.
