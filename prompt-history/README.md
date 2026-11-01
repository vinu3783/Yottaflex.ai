# Project brief, prompt record & delivery plan

This folder documents how the plant-simulation submission was scoped, built and published.
It is intended to give reviewers both the original user prompts and a concise map from the
brief to the delivered work.

> **Transcript integrity:** prompts below are preserved as sent, including spelling and
> grammar. The phase descriptions and delivery map are editorial summaries for readability;
> they are not additional prompts or claims that the prompts were more detailed than they were.
> Assistant responses are not reproduced.

## Project phases

| Phase | Goal | Prompt record |
|---|---|---|
| 1 · Define the brief | Establish the business problem, domain model, deterministic dataset, edge cases, evaluation criteria and lawful-data constraints. | [01 · Project brief](01_brief.md) |
| 2 · Locate the workspace | Point the assistant to the existing project folder and establish which workspace to inspect. | [02 · Workspace check](02_check_workspace.md) |
| 3 · Build and deliver | Request completion of the end-to-end project and publication of the result. | [03 · Build request](03_build_and_push.md) |
| 4 · Resolve publication scope | Clarify repository ownership and confirm that this plant-simulation project—not the earlier API-pipeline task—is the submission to publish. | [04 · Publishing decision](04_publish.md) |

## Delivery plan and evidence

The detailed requirements in the first prompt translate into these reviewable workstreams.
Links point to the implementation or project documentation that records the result.

| Workstream | Intended outcome | Evidence in the project |
|---|---|---|
| Domain model and traceability | Document entities, relationships, IDs, units, lifecycle history and assumptions. | [Data dictionary](../docs/DATA_DICTIONARY.md), [design decisions](../docs/DESIGN_DECISIONS.md) |
| Deterministic plant simulation | Generate connected plant data and reproducible telemetry from a fixed seed. | [Simulation model](../docs/SIMULATION_MODEL.md), [dataset manifest](../data/full/manifest.json) |
| Faults and detection | Inject labelled scenarios separately from detector inputs; explain detection rules and exceptions. | [Evaluation report](../data/full/reports/evaluation.md), [requirements trace](../docs/REQUIREMENTS_TRACE.md) |
| Validation and evaluation | Check integrity, edge cases, reproducibility, detection quality and robustness with measurable outputs. | [Validation and evaluation](../docs/VALIDATION_AND_EVALUATION.md), [test suite](../tests/) |
| Delivery and responsible use | Provide run instructions, disclose AI assistance, and document synthetic-data provenance and limitations. | [Project README](../README.md), [AI usage](../docs/AI_USAGE.md), [limitations](../README.md#limitations) |

The project brief remains the source of requirements. The delivery map is a reviewer aid,
not a substitute for the underlying test results or documentation.
