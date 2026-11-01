# 3. Implement, verify and deliver

> **Refined prompt — editorial reconstruction, not a verbatim transcript.**
> This version turns the implementation request into a testable delivery brief.

```text
Implement the plant-simulation project defined in the project brief as a complete,
maintainable Python repository. First inspect the workspace and follow its existing
conventions where appropriate. Keep the simulator separate from unrelated project work.

Deliver the full and starter datasets, deterministic generator, hydraulic simulation,
fault injection, detector, validation, evaluation, reproducibility checks, robustness
sweep, what-if command, CSV/JSON/SQL exports, tests and reviewer-facing documentation.
Keep ground truth separate from detector inputs. Preserve event history and never
overwrite existing simulation runs.

Verify the acceptance criteria with the project's tests and the smallest relevant lint,
build and runtime checks. Fix failures caused by the implementation. Report the commands
run, outcomes, known limitations and any checks that could not be completed. Do not claim
verification without running it.

Once the result is verified, prepare it for publication in the explicitly selected
repository. Do not replace unrelated repository contents or push changes until the
destination and scope are confirmed.
```
