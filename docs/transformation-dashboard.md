# Transformation Dashboard

The dashboard (`/dashboard.html`, opened from the **Dashboard** button in the app's top bar) is the
steering view of the assessment approach: the assessment (Setup → As-Is → To-Be → Mapping & Gap
Analysis → Summary) builds the information model in Archi, and the dashboard reads that same model to
show where the transformation stands.

Two rules hold for every figure:

1. **It comes from the model.** Values are element properties or are calculated from properties and
   relationships at request time (MCP `get-model-info`, `search-elements`, `search-relationships`).
   Nothing is cached, entered in the dashboard or assumed. Demo values exist only as properties in the
   model.
2. **It follows the governance meta-model** ([backend/app/meta_model.py](../backend/app/meta_model.py)).
   The dashboard only walks relationship pairs the meta-model declares, so a model built by the
   assessment is read the same way as the demo dataset.

Cost, budget, project progress, and KPI measurement are deliberately not in Archi: they belong to the
systems that own them (see [Out of scope](#out-of-scope-and-where-it-belongs)).

- Backend: `GET /api/dashboard?asOf=YYYY-MM-DD` → [backend/app/dashboard.py](../backend/app/dashboard.py)
  (`asOf` defaults to today and drives the time-based checks: end of life, overdue work packages,
  outcome expectations).
- Frontend: [frontend/dashboard.html](../frontend/dashboard.html), [dashboard.js](../frontend/dashboard.js),
  [dashboard.css](../frontend/dashboard.css). Every chart has a **Table** view.
- Tests: `docker run --rm -v "$PWD/backend:/app" -w /app archi-local-chatbot-backend python -m unittest discover -s tests`
  (the test fixture is itself checked against the meta-model).

## Relationships the dashboard follows

All pairs are declared in the meta-model.

| Chain | Used for |
|---|---|
| Outcome –Realization→ Goal, Capability –Realization→ Outcome | Goal progress; which capabilities an outcome depends on |
| BusinessProcess –Realization→ Capability | Capabilities realised by processes |
| ApplicationService –Serving→ BusinessProcess, ApplicationComponent –Realization→ ApplicationService | Application support of processes and, through them, of capabilities; applications without business use |
| BusinessRole –Assignment→ BusinessProcess, BusinessProcess –Triggering→ BusinessProcess | Process views (not used in metrics) |
| Plateau –Realization→ Capability | Capability increments per plateau: is a gap in the roadmap? |
| WorkPackage –Realization→ Plateau | Roadmap: which work packages deliver which plateau |
| Plateau –Association– Gap, Gap –Association– BusinessProcess | Gap register: which gaps a plateau closes and which processes they affect |
| BusinessProcess (To-Be) –Association– BusinessProcess (As-Is) | As-Is → To-Be traceability from the Mapping & Gap Analysis |

## Property schema

Property keys are matched loosely: case, spaces, `-` and `_` are ignored, so `endOfLife`,
`End of Life` and `end_of_life` are the same key. Enumeration values are matched the same way
(`Phase Out` = `phase-out`, `In progress` = `in-progress`). Numbers accept `1.250.000`, `3,5` and leading
numbers such as `3 - Defined`; dates accept `YYYY-MM-DD`, `DD.MM.YYYY`, `YYYY-MM`, `MM/YYYY` and `YYYY`
(partial dates mean the end of that month or year). Elements that miss a property are left out of the
metric that needs it — the **Data completeness** section shows what is missing.

| Element type | Property | Values | Meaning |
|---|---|---|---|
| Goal | `targetDate` | date | Date the goal should be met |
| Outcome | `kpi`, `unit` | text | KPI name and unit (`%`, `days`, `months`, `count`) |
| Outcome | `baseline`, `current`, `target` | number | KPI values at the assessment, now, and targeted |
| Outcome | `direction` | `higher`, `lower` | Whether higher or lower is better (derived from baseline → target when missing) |
| Outcome | `baselineDate`, `targetDate`, `measuredAt` | date | Time frame of the KPI and date of the current value |
| Capability | `capabilityDomain` | text | Group on the capability map (the meta-model has no capability-to-capability relationship) |
| Capability | `maturity`, `targetMaturity` | 1–5 | 1 Initial, 2 Managed, 3 Defined, 4 Quantitatively managed, 5 Optimizing |
| Capability | `strategicImportance` | `high`, `medium`, `low` | Weight in the priority score (3 / 2 / 1) |
| Capability | `owner` | text | Accountable role |
| BusinessProcess | `status` | `current`, `target` | As-Is or To-Be (the assessment writes `target` on To-Be processes; missing = As-Is) |
| BusinessProcess | `processPhase` | text | Phase of the engineering process chain |
| BusinessProcess | `maturity` | 1–5 | Process maturity from the assessment |
| BusinessProcess | `automationLevel` | `manual`, `partial`, `automated` | Degree of automation |
| BusinessProcess | `mediaBreaks` | integer | Tool or data handovers without integration (breaks in the digital thread) |
| ApplicationComponent | `lifecycle` | `plan`, `phase-in`, `active`, `phase-out`, `end-of-life` | Lifecycle phase (SAP LeanIX phases) |
| ApplicationComponent | `endOfLife` | date | Planned or vendor end of life |
| ApplicationComponent | `timeClassification` | `tolerate`, `invest`, `migrate`, `eliminate` | Recorded Gartner TIME decision |
| ApplicationComponent | `functionalFit` | 1–4 | 1 Unreasonable, 2 Insufficient, 3 Appropriate, 4 Perfect |
| ApplicationComponent | `technicalFit` | 1–4 | 1 Inappropriate, 2 Unreasonable, 3 Adequate, 4 Fully appropriate |
| ApplicationComponent | `businessCriticality` | `mission-critical`, `business-critical`, `business-operational`, `administrative` | Criticality class |
| ApplicationComponent | `applicationCategory`, `vendor`, `status` | text | Descriptive |
| Plateau | `targetDate` | date | Date the plateau should be reached |
| WorkPackage | `startDate`, `endDate` | date | Planned span |
| WorkPackage | `status` | `planned`, `in-progress`, `completed`, `on-hold` | Delivery status as recorded in the architecture roadmap |
| WorkPackage | `owner` | text | Accountable role |
| Gap | documentation | text | Description of the gap |

## Metrics

| Section | Metric | Definition |
|---|---|---|
| Motivation | Outcome progress | `(current − baseline) ÷ (target − baseline)` — works for lower-is-better KPIs as well |
| Motivation | Outcome status | Expected progress = time elapsed between `baselineDate` and `targetDate` at `measuredAt`. On track: progress ≥ expected − 10 pts; at risk: ≥ expected − 25 pts; off track: below; missed: target date passed; achieved: ≥ 100 % |
| Motivation | Goal progress | Average (0–100 %) progress of the outcomes realising the goal |
| Strategy | Capability heat map | Capabilities by current maturity, or by gap to target using the TOGAF Business Capabilities guide convention (at target, one level away, two or more levels away) |
| Strategy | Priority gap | `targetMaturity − maturity ≥ 2` on a capability with `strategicImportance = high`; priority score = gap × weight |
| Strategy | Capability support | Share of capabilities realised by a process, and share supported by an application through the service chain |
| Gaps & roadmap | Roadmap status per capability gap | *Not in roadmap*: no plateau realises the capability. *Planned / in delivery / delivered*: status of the work packages realising the earliest plateau that realises it |
| Business | As-Is → To-Be traceability | To-Be processes associated with an As-Is process; As-Is processes covered by a To-Be process; processes associated with a gap |
| Business | Media breaks per phase | Sum of `mediaBreaks` of the rated processes per `processPhase` |
| Application | End-of-life timeline | Applications by `endOfLife`: past, within 12 months, within 24 months, later (calendar months from the as-of date) |
| Application | TIME portfolio | Functional × technical fit; fit ≥ 3 counts as high: Invest (high/high), Migrate (high functional, low technical), Tolerate (low functional, high technical), Eliminate (low/low). A recorded `timeClassification` that differs from the fit quadrant is flagged |
| Application | Application risk | Critical: past end of life. Serious: end of life within 12 months. Watch: end of life within 24 months, or TIME Migrate/Eliminate |
| Application | Without business use | Components realising no application service that serves a business process |
| Implementation & Migration | Work-package flags | Overdue: `endDate` passed and not completed. Ends after its plateau: `endDate` later than the plateau's `targetDate`. Not started yet: `startDate` passed and still planned |
| Implementation & Migration | Plateau completion | Completed work packages ÷ work packages realising the plateau |
| Implementation & Migration | Gap register | Gaps with their affected processes and plateau; gaps without a plateau are not yet planned |

The thresholds above (importance weights, the 2-level priority rule, 12/24-month windows, on-track
tolerances) are method parameters defined in code, not model data.

## Out of scope and where it belongs

| Information | Lives in | How it could reach the dashboard later |
|---|---|---|
| Application run cost, licences | IT financial management / controlling (e.g. SAP CO, Apptio) | Import per application component as a property, or read from that system next to the model |
| Work-package budget, actual cost, percent complete, RAG | Project portfolio management (e.g. Jira Align, Planview, MS Project) | Sync `status` and dates into the work packages; keep financials in PPM |
| Measured KPI values (`current`) | BI / reporting | Periodic import into the Outcome properties |
| Technology lifecycle and vendor support | CMDB / IT asset management | Requires extending the meta-model with a technology layer (Node, SystemSoftware serving ApplicationComponent) |

## Demo dataset

[ops/seed_engineering_demo.py](../ops/seed_engineering_demo.py) seeds the active model with an
engineering / product-development landscape (PLM, ALM, CAD, CAE, MBSE, software toolchain) that uses
only meta-model element types and declared relationship pairs — the script refuses to run otherwise:
127 elements (goals, outcomes, 30 capabilities, processes, roles, application services and components,
3 plateaus, 10 work packages, 8 gaps), 166 relationships and six views (capability map, processes &
roles, application landscape, application services, goals & outcomes, transformation roadmap).
End-of-life dates of named products are the published ones (e.g. Jira Data Center March 2029);
maturity scores, fits, statuses and KPI values are illustrative.

```bash
# Archi running, model open, MCP server started, MCP Server › Approval Mode OFF
python3 ops/seed_engineering_demo.py            # seed into the active model
python3 ops/seed_engineering_demo.py --purge    # remove it again ("Engineering Demo" folders)
```

The Archi MCP plugin always works on the most recently opened model, so open (or close and reopen) the
model you want to seed last.

## Sources

- The Open Group, TOGAF® Series Guide: Business Capabilities — capability heat maps (maturity: at target, one level away, two or more levels away): <https://pubs.opengroup.org/togaf-standard/business-architecture/business-capabilities.html>
- SAP LeanIX, Gartner® TIME model (functional × technical fit quadrants): <https://www.leanix.net/en/wiki/apm/gartner-time-model>
- SAP LeanIX, functional and technical fit scales (1–4): <https://www.leanix.net/en/blog/how-to-generate-immediate-results-in-ea-your-30-day-agenda>
- SAP LeanIX, application criticality classes: <https://www.leanix.net/en/wiki/apm/application-criticality-assessment-and-matrix>
- SAP LeanIX, lifecycle phases: <https://help.sap.com/docs/leanix/ea/obsolescence-risk-management-discover-prioritize-risks>
- EU Machinery Regulation 2023/1230 (applies from 20 January 2027): <https://osha.europa.eu/en/legislation/directive/regulation-20231230eu-machinery>
- EU Cyber Resilience Act (reporting from 11 September 2026, full application 11 December 2027): <https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act>
- Atlassian Data Center end of life (28 March 2029): <https://www.stride.page/blog/jira-data-center-end-of-life>
