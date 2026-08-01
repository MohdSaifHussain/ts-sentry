# Diagrams

Three views of the same system. Rendered natively by GitHub; the source is
Mermaid so the diagrams live in version control rather than in an image nobody
can edit.

## 1. The governance spine

The path every agent output takes, and the one path it cannot take. Read left to
right: an agent proposes, deterministic code disposes, a human governs.

```mermaid
flowchart LR
    subgraph Mandate["Mandate (frozen, hashed, versioned)"]
        M1["consequence_ceiling<br/>OBSERVE | ASSEMBLE | RECOMMEND"]
        M2["allowed_tools<br/>data_scopes<br/>token_budget, max_steps"]
    end

    A["Agent<br/>proposes an action"] --> FW["Input firewall<br/>case content is inert data"]
    FW --> V{"Mandate check<br/>before dispatch"}
    Mandate -.governs.-> V

    V -->|"outside the mandate"| REF["Refused, never executed<br/>MANDATE_VIOLATION_ATTEMPT"]
    V -->|"inside"| D["Dispatch<br/>allowlisted tool table"]
    D --> S["Output schema check"]
    S --> G{"Consequence gate"}

    G -->|OBSERVE| OK["Auto-approved"]
    G -->|ASSEMBLE| AS["Referential integrity<br/>provenance completeness"]
    G -->|RECOMMEND| RC["Every claim resolves to<br/>an evidence-record id"]
    G -->|"ENFORCE"| X["UNREACHABLE<br/>no Mandate can carry it"]

    AS -->|fails| RJ["GATE_REJECTION<br/>VERIFICATION_FAIL"]
    RC -->|fails| RJ
    OK --> L["Ledger append<br/>hash-chained"]
    AS -->|passes| L
    RC -->|passes| L
    RJ --> L
    REF --> L

    L --> AN["Delivered to the analyst"]
    AN --> H["Human signature<br/>the only route to ENFORCE"]

    style X fill:#3a1414,stroke:#c0392b,color:#f5b7b1
    style REF fill:#3a2a14,stroke:#b9770e,color:#f8d7a3
    style RJ fill:#3a2a14,stroke:#b9770e,color:#f8d7a3
    style H fill:#14321f,stroke:#1e8449,color:#a9dfbf
    style L fill:#152a3a,stroke:#2874a6,color:#aed6f1
```

`ENFORCE` is drawn as a terminal box with no inbound edge from the gate on
purpose. It is not blocked at runtime; it is unreachable at type level, because
`Consequence.ENFORCE` is excluded from the `AgentConsequence` alias every
`Mandate` is built from. The claim this diagram makes is narrow and exact: **no
agent action can reach the ENFORCE gate.** It is not that the enum member cannot
be named.

## 2. The fleet under the orchestrator

The prohibited topology matters more than the permitted one. There is no edge
between any two agents, and that absence is enforced by an import-graph test,
not by convention.

```mermaid
flowchart TB
    H(["Analyst (human supervisor)<br/>sole enforcement authority"])

    subgraph ORC["Orchestrator: deterministic, synchronous, sole executor"]
        direction LR
        O1["session lifecycle"]
        O2["dispatch + firewall"]
        O3["consequence gates"]
        O4["ledger + routing"]
    end

    T["Triage agent<br/>ceiling OBSERVE"]
    E["Evidence agent<br/>ceiling ASSEMBLE"]
    M["Memo agent<br/>ceiling RECOMMEND"]
    P["Prompt-eval agent<br/>ceiling OBSERVE"]

    H <--> ORC
    ORC <--> T
    ORC <--> E
    ORC <--> M
    ORC <--> P

    T -. "prohibited" .-x E
    E -. "prohibited" .-x M
    M -. "prohibited" .-x P

    DB[("DuckDB<br/>allowlisted scopes only")]
    SEALED[("sealed._labels<br/>measurement only")]
    ORC --> DB
    ORC -.->|"no scope resolves here"| SEALED

    style SEALED fill:#3a1414,stroke:#c0392b,color:#f5b7b1
    style H fill:#14321f,stroke:#1e8449,color:#a9dfbf
    style ORC fill:#152a3a,stroke:#2874a6,color:#aed6f1
```

Halting the orchestrator halts the fleet, because it is the only thing that
executes anything. There is no background autonomy to chase down.

## 3. Session dataflow

One evidence session, end to end. This is what the artifacts in
[`examples/02-evidence-t02-ring`](../examples/02-evidence-t02-ring/) record.

```mermaid
sequenceDiagram
    autonumber
    participant An as Analyst
    participant Or as Orchestrator
    participant Ag as Evidence agent
    participant Db as DuckDB
    participant Lg as Ledger

    An->>Or: run-session --agent evidence --subject X
    Or->>Db: does X exist?
    Note over Or,Db: refused before any session<br/>directory or chain exists
    Or->>Lg: SESSION_OPEN<br/>(mandates, dataset digest, model provenance)

    loop each hop, up to max_steps
        Or->>Ag: pack so far, fenced as inert data
        Or->>Lg: PROMPT_SENT
        Ag-->>Or: proposed pivot + typed params
        Or->>Or: in the mandate? params in bounds?<br/>entity already in the pack?
        Or->>An: approve or reject this pivot
        An-->>Or: decision
        Or->>Lg: HUMAN_DECISION (reviewer_kind)
        Or->>Db: parameterized template, never composed SQL
        Db-->>Or: rows
        Or->>Lg: TOOL_CALLED, TOOL_RESULT
        Or->>Or: ASSEMBLE gate over the grown pack
        Or->>Lg: VERIFICATION_PASS or GATE_REJECTION
    end

    Or->>Lg: SESSION_CLOSE
    Or->>An: evidence_pack.json, graphml,<br/>ledger.jsonl, session_manifest.json
    Note over An: verify-ledger --expect-head-from<br/>checks the chain against its anchor
```

The agent never touches the database and never writes SQL. It names a template
and supplies typed parameters; the orchestrator runs it. Any entity id the agent
supplies must already be in the pack, so an investigation expands outward from
the analyst's chosen seed rather than reaching anywhere it likes.
