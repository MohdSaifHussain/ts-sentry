# Prompt evaluation: classify.threat_class

**Decision: REFUSED**

- Incumbent: `71610d32c1e6c5b0`
- Candidate: `334dd1b70e9ee0d7`
- Items: 59
- Answered by: `stub/faithful` / `deterministic-stub-v1`

## Why activation was refused

- **recall_regression** (t01_comment_spam_ring): t01_comment_spam_ring: recall fell 0.667 (0.667 to 0.000) on 6 item(s), and the 95% interval lower bound -1.000 is beyond the tolerated drop of 0.250
- **recall_regression** (t02_fake_engagement_network): t02_fake_engagement_network: recall fell 0.500 (0.500 to 0.000) on 12 item(s), and the 95% interval lower bound -0.750 is beyond the tolerated drop of 0.250
- **recall_regression** (t04_undisclosed_synthetic_media): t04_undisclosed_synthetic_media: recall fell 0.500 (0.500 to 0.000) on 4 item(s), and the 95% interval lower bound -1.000 is beyond the tolerated drop of 0.250
- **recall_regression** (t07_coordinated_influence_op): t07_coordinated_influence_op: recall fell 0.500 (0.500 to 0.000) on 6 item(s), and the 95% interval lower bound -0.833 is beyond the tolerated drop of 0.250
- **macro_f1_regression** (overall): macro F1 fell 0.287 (0.338 to 0.051), beyond the tolerated 0.100. Reported as a point estimate: this is an aggregate over classes rather than a per-item quantity, so the paired item bootstrap does not apply to it

## Per-class results

| class | support | incumbent recall | candidate recall | delta | 95% CI | min detectable drop |
|---|---|---|---|---|---|---|
| benign | 15 | 0.867 | 1.000 | +0.133 | [+0.000, +0.333] | 0.167 |
| t01_comment_spam_ring | 6 | 0.667 | 0.000 | -0.667 | [-1.000, -0.333] | 0.333 |
| t02_fake_engagement_network | 12 | 0.500 | 0.000 | -0.500 | [-0.750, -0.250] | 0.250 |
| t03_off_platform_diversion | 6 | 0.000 | 0.000 | +0.000 | [+0.000, +0.000] | 0.000 |
| t04_undisclosed_synthetic_media | 4 | 0.500 | 0.000 | -0.500 | [-1.000, +0.000] | 0.500 |
| t05_ai_persona_authority | 4 | 0.000 | 0.000 | +0.000 | [+0.000, +0.000] | 0.000 |
| t06_slop_farm | 6 | 0.000 | 0.000 | +0.000 | [+0.000, +0.000] | 0.000 |
| t07_coordinated_influence_op | 6 | 0.500 | 0.000 | -0.500 | [-0.833, -0.167] | 0.333 |

## Precision and F1, per class

| class | support | incumbent precision | incumbent F1 | candidate precision | candidate F1 |
|---|---|---|---|---|---|
| benign | 15 | 0.500 | 0.634 | 0.254 | 0.405 |
| t01_comment_spam_ring | 6 | 1.000 | 0.800 | 0.000 | 0.000 |
| t02_fake_engagement_network | 12 | 1.000 | 0.667 | 0.000 | 0.000 |
| t03_off_platform_diversion | 6 | 0.000 | 0.000 | 0.000 | 0.000 |
| t04_undisclosed_synthetic_media | 4 | 0.182 | 0.267 | 0.000 | 0.000 |
| t05_ai_persona_authority | 4 | 0.000 | 0.000 | 0.000 | 0.000 |
| t06_slop_farm | 6 | 0.000 | 0.000 | 0.000 | 0.000 |
| t07_coordinated_influence_op | 6 | 0.250 | 0.333 | 0.000 | 0.000 |

Macro F1: incumbent 0.338, candidate 0.051.

Unparseable answers: incumbent 0, candidate 0.

## How to read these numbers

- Precision here is not a deployment estimate. This eval set deliberately over-samples rare classes against a platform that is more than 97% benign (ARCHITECTURE 6.1). Per-class recall is a within-class quantity and is unaffected by that choice; precision moves with prevalence and is not.
- This gate detects a class collapse, not a few-point drift. The generator plants 4 to 12 entities per threat class regardless of --scale, so a class's recall moves in steps of a quarter to a twelfth and no tolerance setting can resolve anything finer. The bound is a property of the data, not of the gate.

## Reproducing this run

- Dataset seed: `42`, scale `1`
- Eval items: `ed9e86354f05550db6c3b9445b293fcac283415641bb4416f6a2ea3128e9a77c`
- Eval labels: `0e69188afa1245dc7c2c1944990f63bc68a0983f862182aa40667dfbaf79e8db`
- Bootstrap seed: `42` over 2000 resamples
- Tolerances: `78e2b8fb0db8a7a6147d03208d0a7c0e2fc7367971d167baa953c677f4f5f3fb`
- Code: `63565420eb8c48f0c106465e123ac7d10c2b00a9`
