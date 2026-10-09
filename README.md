# vidai

MasSurge MVP creative pipeline — product URL → audience → selling angle → character → Speech + Visual
script (QC + repair) → shot plan → talking-head clips with speech (local LTX-2.5) → QC + repair → final video.

Everything lives in [`agent/`](agent/) (Python package `vidai`, run commands from inside it); see
`agent/README.md` for setup, usage and the per-step output layout. Secrets stay in `agent/.env`
(never committed).
