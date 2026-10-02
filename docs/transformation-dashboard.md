# Transformation Dashboard

The dashboard (`/dashboard.html`, opened from the **Dashboard** button in the app's top bar) shows
capability-based planning and transformation-steering metrics for the model that is currently active
in Archi. Every figure is computed at request time from the model's elements, their properties and
their relationships, read through the Archi MCP server (`get-model-info`, `search-elements`,
`search-relationships`). Nothing is cached and nothing is entered in the dashboard itself — to change a
number, change the model.

- Backend: `GET /api/dashboard?asOf=YYYY-MM-DD` → [backend/app/dashboard.py](../backend/app/dashboard.py)
  (`asOf` defaults to today; it drives every time-based metric: end of life, support runway, planned
  progress, outcome expectations).
- Frontend: [frontend/dashboard.html](../frontend/dashboard.html), [dashboard.js](../frontend/dashboard.js),
  [dashboard.css](../frontend/dashboard.css). Every chart has a **Table** view; tooltips only repeat
  values that are also in the chart labels or the table.
- Tests: `docker run --rm -v "$PWD/backend:/app" -w /app archi-local-chatbot-backend python -m unittest discover -s tests`

## Property schema

Property keys are matched loosely: case, spaces, `-` and `_` are ignored, so `endOfLife`,
`End of Life` and `end_of_life` are the same key. Enumeration values are matched the same way
(`Phase Out` = `phase-out`). Numbers accept `1.250.000`, `1,250,000`, `3,5` and leading numbers such as
`3 - Defined`; dates accept `YYYY-MM-DD`, `DD.MM.YYYY`, `YYYY-MM`, `MM/YYYY` and `YYYY` (partial dates mean
the end of that month or year). Elements that miss a property are left out of the metric that needs
it — the **Data completeness** section shows what is missing.

| Element type | Property | Values | Meaning |
|---|---|---|---|
| Capability | `capabilityLevel` | `L1`, `L2` | Informational; the hierarchy itself comes from Composition/Aggregation between capabilities, with `parentCapability` (name) as fallback |
| Capability (leaf) | `maturity` | 1–5 | Current maturity: 1 Initial, 2 Managed, 3 Defined, 4 Quantitatively managed, 5 Optimizing (CMMI-style levels) |
| Capability (leaf) | `targetMaturity` | 1–5 | Maturity the capability must reach for the target state |
| Capability (leaf) | `strategicImportance` | `high`, `medium`, `low` | Weight in the priority score (3 / 2 / 1) |
| Capability | `owner` | text | Accountable role |
| ValueStream (stage) | `stageOrder` | integer | Position of the stage in the value stream |
| BusinessProcess | `maturity` | 1–5 | Process maturity |
| BusinessProcess | `automationLevel` | `manual`, `partial`, `automated` | Degree of automation |
| BusinessProcess | `mediaBreaks` | integer | Tool or data handovers without integration (breaks in the digital thread) |
| BusinessProcess | `valueStreamStage` | stage name | Value-stream stage the process belongs to |
| ApplicationComponent | `lifecycle` | `plan`, `phase-in`, `active`, `phase-out`, `end-of-life` | Lifecycle phase (SAP LeanIX phases) |
| ApplicationComponent | `endOfLife` | date | Planned or vendor end of life |
| ApplicationComponent | `timeClassification` | `tolerate`, `invest`, `migrate`, `eliminate` | Recorded Gartner TIME decision |
| ApplicationComponent | `functionalFit` | 1–4 | 1 Unreasonable, 2 Insufficient, 3 Appropriate, 4 Perfect |
| ApplicationComponent | `technicalFit` | 1–4 | 1 Inappropriate, 2 Unreasonable, 3 Adequate, 4 Fully appropriate |
| ApplicationComponent | `businessCriticality` | `mission-critical`, `business-critical`, `business-operational`, `administrative` | Criticality class |
| ApplicationComponent | `annualCostEUR` | number | Annual run cost |
| ApplicationComponent | `applicationCategory`, `vendor`, `users`, `hosting`, `status` | text / number | Descriptive |
| Node, Device, SystemSoftware, TechnologyService | `lifecycle` | as above | Lifecycle phase |
| Node, Device, SystemSoftware, TechnologyService | `vendorSupportEnd` | date | End of vendor (general) support |
| Node, Device, SystemSoftware, TechnologyService | `techRadar` | `adopt`, `trial`, `assess`, `hold` | Technology-radar ring |
| Node, Device, SystemSoftware, TechnologyService | `technologyCategory`, `vendor`, `version`, `hosting` | text | Descriptive |
| Goal | `targetDate` | date | Date the goal should be met |
| Outcome | `kpi`, `unit` | text | KPI name and unit (`%`, `days`, `months`, `count`) |
| Outcome | `baseline`, `current`, `target` | number | KPI values |
| Outcome | `direction` | `higher`, `lower` | Whether higher or lower is better (derived from baseline → target when missing) |
| Outcome | `baselineDate`, `targetDate`, `measuredAt` | date | Time frame of the KPI and date of the current value |
| Plateau | `targetDate` | date | Date the plateau is reached |
| WorkPackage | `startDate`, `endDate` | date | Planned span |
| WorkPackage | `progress` | 0–100 | Percent complete |
| WorkPackage | `budgetEUR`, `actualCostEUR` | number | Budget at completion and actual cost to date |
| WorkPackage | `rag` | `green`, `amber`, `red` | Reported health |
| WorkPackage | `plateau`, `owner` | text | Plateau the package delivers into; accountable role |

### Relationships the metrics follow

| Relationship | Used for |
|---|---|
| Capability –Composition/Aggregation→ Capability | Capability hierarchy (heat-map groups) |
| ApplicationComponent –Realization→ Capability | Applications supporting a capability (fallback when a capability has none: ApplicationComponent –Serving→ BusinessProcess –Realization→ Capability) |
| Node/Device/SystemSoftware –Serving→ ApplicationComponent | Technology risk propagated to applications |
| ApplicationComponent –Serving→ BusinessProcess | Process scorecard, processes without application support |
| Capability –Serving→ ValueStream | Maturity along the value stream |
| WorkPackage –Association→ Capability (or WorkPackage –Realization→ Deliverable –Realization→ Capability) | Investment per capability; gap coverage |
| Outcome –Realization→ Goal, Capability –Realization→ Outcome, Driver –Influence→ Goal | Goal progress, drivers, outcome realisation |

## Metrics

| Layer | Metric | Definition |
|---|---|---|
| Strategy | Capability maturity heat map | Leaf capabilities by current maturity, or by gap to target using the TOGAF Business Capabilities guide convention: at target, one level away, two or more levels away |
| Strategy | Priority gap | `targetMaturity − maturity ≥ 2` on a capability with `strategicImportance = high` |
| Strategy | Priority score | `gap × weight(importance)`, weights high 3 / medium 2 / low 1 |
| Strategy | Application support coverage | Share of leaf capabilities with at least one supporting application |
| Cross-layer | Priority gaps vs. investment | Gap, importance and the work-package budget associated with each capability (a package's budget is split evenly across the capabilities it changes); a priority gap without a work package is *unaddressed* |
| Business | Media breaks per value-stream stage | Sum of `mediaBreaks` over the processes of the stage |
| Business | Automation level per stage | Count of processes per `automationLevel` |
| Application | End-of-life timeline | Applications by `endOfLife`: past, within 12 months, within 24 months, later (calendar months from the as-of date) |
| Application | TIME portfolio | Applications placed by functional × technical fit; fit ≥ 3 counts as high: Invest (high/high), Migrate (high functional, low technical), Tolerate (low functional, high technical), Eliminate (low/low). A recorded `timeClassification` that differs from the fit quadrant is flagged |
| Application | Cost by TIME decision | Sum of `annualCostEUR` per recorded TIME class; share in Migrate + Eliminate |
| Application | Application risk | Critical: past end of life or served by out-of-support technology. Serious: end of life or technology support end within 12 months. Watch: end of life within 24 months, or TIME Migrate/Eliminate |
| Technology | Vendor support runway | Months between the as-of date and `vendorSupportEnd`; out of support when the date has passed (or lifecycle is end of life without a date) |
| Technology | Out-of-support share | Out-of-support platforms ÷ platforms with a support date |
| Technology | Exposed applications | Applications served by out-of-support technology, ordered by criticality |
| Motivation | Outcome progress | `(current − baseline) ÷ (target − baseline)` — works for lower-is-better KPIs as well |
| Motivation | Outcome status | Expected progress = time elapsed between `baselineDate` and `targetDate` at `measuredAt`. On track: progress ≥ expected − 10 pts; at risk: ≥ expected − 25 pts; off track: below that; missed: target date passed; achieved: progress ≥ 100 % |
| Motivation | Goal progress | Average (0–100 %) progress of the outcomes realising the goal |
| Implementation & Migration | Planned progress | Share of the planned span elapsed at the as-of date |
| Implementation & Migration | Earned value | `budgetEUR × progress` |
| Implementation & Migration | SPI, CPI, EAC | SPI = earned value ÷ planned value; CPI = earned value ÷ actual cost; EAC = budget ÷ CPI (PMI earned-value formulas) |
| Implementation & Migration | Computed health | Red when overdue, SPI < 0.8 or CPI < 0.8; amber when SPI or CPI < 0.95 — shown next to the reported `rag` in the tooltip and table |
| Implementation & Migration | Roadmap progress (headline) | Total earned value ÷ total budget, against total planned value ÷ total budget |

## Demo dataset

[ops/seed_engineering_demo.py](../ops/seed_engineering_demo.py) seeds the active model with an
engineering / product-development landscape (PLM, ALM, CAD, CAE, MBSE, software toolchain): 135
elements, 266 relationships and one view per layer (capability map, value stream & processes,
application landscape, technology infrastructure, motivation & KPIs, transformation roadmap). Vendor
support and end-of-life dates of the named products are the published ones (e.g. Windows 10 Oct 2025,
Windows Server 2012 R2 Oct 2023, RHEL 7 Jun 2024, Oracle 12.2 Mar 2022, vSphere 8 Oct 2027, Jira Data
Center Mar 2029); maturity scores, fits, costs, budgets and KPI values are illustrative.

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
- SAP LeanIX, obsolescence risk management and lifecycle phases: <https://help.sap.com/docs/leanix/ea/obsolescence-risk-management-discover-prioritize-risks>
- PMI, earned value (SPI, CPI): <https://www.pmi.org/learning/library/make-earned-value-work-project-6001>
- EU Machinery Regulation 2023/1230 (applies from 20 January 2027): <https://osha.europa.eu/en/legislation/directive/regulation-20231230eu-machinery>
- EU Cyber Resilience Act (reporting from 11 September 2026, full application 11 December 2027): <https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act>
- Atlassian Data Center end of life (28 March 2029): <https://www.stride.page/blog/jira-data-center-end-of-life>
- VMware vSphere 8 end of general support (11 October 2027): <https://nexstor.com/vmware-vsphere-8-support-ends-october-2027-what-to-do-before-the-deadline-arrives/>
