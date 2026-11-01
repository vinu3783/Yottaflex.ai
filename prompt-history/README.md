# Refined project prompts

This folder contains a polished, project-specific prompt set describing the plant
simulation's scope, workspace assessment, implementation and publication. It is designed
to help reviewers understand the work and its acceptance criteria.

> **Important:** these are editorial reconstructions, not verbatim historical prompts.
> They were rewritten after implementation to organize the project's actual scope into a
> clear prompt sequence. They must not be read as a transcript or as evidence that each
> detailed requirement was originally requested in these exact words. The project's
> implementation and test results are documented separately.

## Project phases

| Phase | Goal | Prompt |
|---|---|---|
| 1 · Define the project | Specify the simulation, dataset, fault scenarios, evaluation and acceptance criteria. | [Project brief](01_brief.md) |
| 2 · Inspect the workspace | Establish whether the existing project matches the brief and set a safe project boundary. | [Workspace assessment](02_check_workspace.md) |
| 3 · Implement and verify | Build the project end to end and verify it against measurable acceptance criteria. | [Build and delivery](03_build_and_push.md) |
| 4 · Publish | Confirm repository scope, preserve the earlier project backup, and verify publication. | [Publishing](04_publish.md) |

## Delivery map

The prompts summarize the intended project work. These links provide evidence of the
implemented behavior and its documented limitations.

| Workstream | Evidence |
|---|---|
| Domain model and traceability | [Data dictionary](../docs/DATA_DICTIONARY.md), [design decisions](../docs/DESIGN_DECISIONS.md) |
| Deterministic simulation and dataset | [Simulation model](../docs/SIMULATION_MODEL.md), [dataset manifest](../data/full/manifest.json) |
| Fault injection, detection and scoring | [Evaluation report](../data/full/reports/evaluation.md), [requirements trace](../docs/REQUIREMENTS_TRACE.md) |
| Validation and automated tests | [Validation and evaluation](../docs/VALIDATION_AND_EVALUATION.md), [test suite](../tests/) |
| Setup, provenance and limitations | [Project README](../README.md), [AI usage disclosure](../docs/AI_USAGE.md) |

The original project brief remains the source of requirements. The prompts here are an
edited presentation of that scope, not a substitute for source code, reports or test results.
