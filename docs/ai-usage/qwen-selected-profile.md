# Frozen Qwen candidate profile

The four completed DEV runs used the identical solver at
`332a43ebd10128de1bdf0cb597c35cdf66d46c39` and 840 synthetic development cases.
Each configuration ran once on the same Windows evaluation path. Raw strings
were compared without trimming, numeric normalization or case folding.

| Policy | Output cap | Correct | Wrong | Blank | Seconds/question | Measured tokens/question |
|---|---:|---:|---:|---:|---:|---:|
| fallback | 600 | 601 | 214 | 25 | 0.702839 | 623.303571 |
| fallback | 1200 | 602 | 215 | 23 | 0.719069 | 626.305952 |
| selective | 600 | 624 | 187 | 29 | 2.948563 | 2746.932143 |
| selective | 1200 | 619 | 193 | 28 | 3.020837 | 2757.751190 |

OBSERVED: selective/600 has the highest correct count, so the preregistered
correctness-first rule selects it. Its total reported cost was USD 0.081298678,
with no unresolved billing. These are development selection results, not a
blind accuracy estimate or a claim about the organizer's hidden data. One run
per configuration does not estimate model variability.

The final packaging change sets the selected policy and token cap in Docker's
environment and pins packaging==26.3, the transitive dependency observed in
both the tested Windows environment and the Linux transport smoke. No run.py,
src, prompt, planner or mathematical execution bytes change after DEV.
Direct Python invocation outside Docker requires the same QWEN_* variables.
The six contest CLI arguments remain supported.

The paired sealed protocol records the actual policy separately for each arm:
fallback/600 for baseline and selective/600 for candidate. Both use the same
candidate build recipe, pinned runtime dependencies and 600-token ceiling.
Baseline answering source remains `8188df35c181992fd0aa940dd4bb827370084cb4`.
Runtime images, prompt hashes and source/configuration receipts are frozen
before any sealed input is opened. No sealed outcome is claimed here.

Public 50/50, final Linux checks and the paired sealed comparison remain
acceptance gates. Detailed private receipts stay outside Git and the submission
ZIP. Main merge and contest submission are separate actions.
