# Data Dictionary

Trust & Safety Sentry synthetic platform dataset (Phase 1 / STEP-01). One
row per column, across the six queryable entity tables (`main` schema,
each reachable through a `DataScope` member) and the sealed ground-truth
table (`sealed._labels`). Types are DuckDB column types; enums are stored
as `VARCHAR` holding the `StrEnum` member's string value.

All timestamp columns are `TIMESTAMPTZ`, always `Asia/Kolkata` (IST),
serialized ISO 8601 at the Parquet/JSON export boundary. Every table's
primary key and foreign keys are declared in `ts_sentry.data.store`.

> **Currency, checked at release rather than assumed (STEP-08).** No phase after
> STEP-01 changed this schema, and that was verified by reading
> `information_schema` out of a real seed-42 build and comparing it against this
> file column by column: **seven tables, zero undocumented columns.**
> `engagement_event.session_id` is still reserved and still `NULL` on every row.
> The later phases added artifacts (session manifests, evidence packs, memos,
> eval sets, measurement reports) rather than dataset columns, and those are
> documented where they are produced rather than here.

## `main.account_meta`

| Column | Type | Unit | Nullable | Provenance |
|---|---|---|---|---|
| account_id | VARCHAR (PK) | - | no | Generated (`acct_NNNNNNN` base population; `t0N_acct_...` planted) |
| created_ts | TIMESTAMPTZ | IST | no | Drawn from the Poisson-burst mixture (see Assumptions) |
| display_name | VARCHAR | - | no | Synthetic name + random suffix, or ring-specific handle |
| is_verified | BOOLEAN | - | no | Bernoulli(0.02) for base population; always `false` for planted accounts |
| signup_ip_bucket | VARCHAR | - | no | Coarse bucket id, not a real IP; small pool (40) for base population so benign accounts naturally overlap |
| device_fingerprint_hint | VARCHAR | - | yes | `NULL` for base population; set for some planted rings (e.g. T-01, T-06) as the shared-device signal |

## `main.channel`

| Column | Type | Unit | Nullable | Provenance |
|---|---|---|---|---|
| channel_id | VARCHAR (PK) | - | no | Generated |
| account_id | VARCHAR (FK -> account_meta) | - | no | Owning account |
| created_ts | TIMESTAMPTZ | IST | no | Poisson-burst mixture |
| display_name | VARCHAR | - | no | Synthetic name |
| subscriber_count | INTEGER | count | no | Uniform synthetic count (base) or small synthetic count (planted) |
| description | VARCHAR | - | no | Synthetic, topic-templated text |

## `main.video`

| Column | Type | Unit | Nullable | Provenance |
|---|---|---|---|---|
| video_id | VARCHAR (PK) | - | no | Generated |
| channel_id | VARCHAR (FK -> channel) | - | no | Publishing channel |
| title | VARCHAR | - | no | Synthetic, topic-templated |
| description | VARCHAR | - | no | Synthetic, topic-templated |
| published_ts | TIMESTAMPTZ | IST | no | Poisson-burst mixture |
| duration_s | INTEGER | seconds | no | Uniform(30, 1800) |
| synthetic_media_disclosed | BOOLEAN | - | no | Whether the (synthetic) publisher marked the video as AI-generated |
| provenance_signal | VARCHAR (enum: present/absent/unknown) | - | no | C2PA-direction signal (ARCHITECTURE 8.7); `absent` is a deliberate nondisclosure marker used by T-04/T-05, not the default for undecided content (`unknown`) |

## `main.comment`

| Column | Type | Unit | Nullable | Provenance |
|---|---|---|---|---|
| comment_id | VARCHAR (PK) | - | no | Generated |
| video_id | VARCHAR (FK -> video) | - | no | Commented-on video |
| account_id | VARCHAR (FK -> account_meta) | - | no | Commenting account |
| parent_comment_id | VARCHAR (FK -> comment) | - | yes | `NULL` unless a reply |
| posted_ts | TIMESTAMPTZ | IST | no | Poisson-burst mixture |
| text | VARCHAR | - | no | Synthetic, or templated spam/lure/narrative text for planted rows |
| template_id | VARCHAR | - | yes | `NULL` for base population; set when a threat generator reuses one template across a ring (T-01) |

## `main.engagement_event`

| Column | Type | Unit | Nullable | Provenance |
|---|---|---|---|---|
| event_id | VARCHAR (PK) | - | no | Generated; doubles as the VVR `view_id` when `kind = view` |
| kind | VARCHAR (enum: view/like/dislike/share/subscribe/report) | - | no | Determines which of video_id/channel_id is populated (`__post_init__`-enforced) |
| account_id | VARCHAR (FK -> account_meta) | - | no | Acting account; the VVR `viewer_account_id` when `kind = view` |
| video_id | VARCHAR (FK -> video) | - | yes | Set for view/like/dislike/share/report; `NULL` for subscribe |
| channel_id | VARCHAR (FK -> channel) | - | yes | Set for subscribe; `NULL` otherwise |
| ts_ist | TIMESTAMPTZ | IST | no | Poisson-burst mixture; the VVR `ts_ist` field |
| session_id | VARCHAR | - | yes | Reserved; always `NULL` in Phase 1 |

## `main.infra_hint`

| Column | Type | Unit | Nullable | Provenance |
|---|---|---|---|---|
| hint_id | VARCHAR (PK) | - | no | Generated |
| subject_kind | VARCHAR (enum: account/video/comment/channel) | - | no | Which entity the signal attaches to |
| subject_id | VARCHAR (FK by subject_kind) | - | no | The entity carrying the signal |
| signal_type | VARCHAR (enum: shared_upload_pattern/template_reuse/shared_device/shared_ip_bucket/link_domain_reuse) | - | no | Evidence-pivot category |
| signal_value | VARCHAR | - | no | The shared cluster/bucket/domain value that groups colluding entities |
| observed_ts | TIMESTAMPTZ | IST | no | Poisson-burst mixture |

## `sealed._labels` (sealed schema)

Access: build pipeline only (write, and the D6 AnalystKit `reconcile` gate
read); from STEP-07 onward, measurement code. Never reachable via
`DataScope` - no member resolves to `sealed`.

| Column | Type | Unit | Nullable | Provenance |
|---|---|---|---|---|
| entity_kind | VARCHAR (enum: account/channel/video/comment) (PK part) | - | no | Which table `entity_id` belongs to |
| entity_id | VARCHAR (PK part) | - | no | The labeled entity's id |
| threat_class | VARCHAR (enum: benign/t01..t07) | - | no | Ground truth; `benign` for every untouched base-population entity |
| ring_id | VARCHAR | - | yes | Groups colluding planted entities for the STEP-04 network-recovery metric; `NULL` for benign rows |
| planted_ts | TIMESTAMPTZ | IST | no | When the generator planted (or, for benign, labeled) this entity |
| generator_params_hash | VARCHAR | - | no | SHA-256 prefix of the threat generator's parameter dataclass, for traceability; literal `base` for benign rows |

## Assumptions (Honest Limits discipline)

- **Benign majority**: every threat class is budgeted so that, combined,
  planted (abusive) accounts/channels/videos/comments target at most 2% of
  the labelable population (`DEFAULT_TOTAL_ABUSE_FRACTION`,
  `ts_sentry.data.threats.common`), against the STEP-01 3.4 floor of >= 97%
  benign - a deliberate safety margin, not a target run close to the wire.
  Engagement events and infra hints are signals, not labeled entities, and
  do not count toward this budget.
- **Burst shaping**: a documented Poisson-burst mixture, not a Hawkes
  process. Every timestamp is drawn either from a short, coordinated burst
  window (probability = burst weight) or uniformly across the full
  deterministic build window (background rate). This is simpler to
  implement, tune, and keep byte-stable across rebuilds than a
  self-exciting kernel, while still producing visibly clustered ring
  activity. Recorded per STEP-01 3.4 in this phase's Outcome section.
- **Base-population scale**: `scale` is an integer multiplier on named base
  constants (`ts_sentry.data.generator`): `BASE_ACCOUNTS = 400`,
  `BASE_CHANNELS = 50`, `MEAN_VIDEOS_PER_CHANNEL = 8`,
  `MEAN_COMMENTS_PER_VIDEO = 15`, engagement means per video
  (`MEAN_VIEWS_PER_VIDEO = 50`, `MEAN_LIKES_PER_VIDEO = 12`,
  `MEAN_DISLIKES_PER_VIDEO = 2`, `MEAN_SHARES_PER_VIDEO = 4`,
  `MEAN_REPORTS_PER_VIDEO = 1`), `MEAN_SUBSCRIBES_PER_CHANNEL = 30`,
  `INFRA_HINT_ACCOUNT_RATE = 0.05`, `IP_BUCKET_POOL_SIZE = 40`. These sizes
  were chosen for build speed and demo legibility, not to match any
  measured platform distribution.
- **No statistical-fidelity claim**: nothing in this dataset is fit to, or
  claims to reproduce, real YouTube distributions. It is a synthetic
  fixture for exercising the governance and agent layers in later phases.
- **Noisy labels / rater disagreement**: not implemented in Phase 1
  (ARCHITECTURE 6.1 realism levers: noisy-labels option, rater-disagreement
  simulation are roadmap, not shipped here). Every planted entity's label
  is exact ground truth.
