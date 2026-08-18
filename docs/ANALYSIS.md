# Printorian — Analysis

What the scenario actually requires, decomposed: every line of it turned into a
capability, with the difficulty named rather than assumed.

## What the scenario requires

The scenario describes two products joined by an automation spine:

```
CUSTOMER SIDE                    SPINE                      FARM SIDE
configurator ──► transparent ──► order ──► prep ──► auto- ──► print ──► post- ──► ship
                 price delta               (slice)  dispatch          production
                                             ▲         ▲                  ▲
                                             │         │                  │
                                        engineer   scheduler         personnel
                                                   + wait list       dashboard
```

### Requirement decomposition

| # | Scenario line | What it actually demands | Difficulty |
|---|---|---|---|
| C1 | Select model from base or upload own | Model library + upload + mesh analysis (volume, bbox, manifold check) | Medium |
| C2 | Material by type **or by usage scenario dialog** | A recommendation engine mapping use-case → material properties → in-stock specs | Medium |
| C2 | Up to 4 colors for multicolor | Multi-material spec on the order; AMS slot planning downstream | Medium |
| C2 | Resize (adds engineer labor) | Scale factor as a priced option with a labor line | Low |
| C2 | Extra-quick production | Rush as a priced option **and** a scheduling priority | Medium |
| C2 | Post-production options list | Catalog of finishing operations, each with labor time + consumables + lead-time impact | Medium |
| C2 | Quantity with tiered discounts | Declarative discount ladder ("every 10 gets cheaper") | Low |
| C3 | **Transparent price structure** | Itemized breakdown with the *basis* of each line, not just a number | Medium |
| C4 | **Delta preview per option change** ("+120 P labor, −260 P material") | Pricing must be a pure function so two specs can be priced and diffed | **Design-critical** |
| C5 | Register + pay | Auth + Russian payment gateway + receipts | Medium |
| C6 | System finds a free appropriate printer | **Scheduler with capability matching** | **Hard** |
| C7 | No free printer → wait list | Wait list + predicted start time from a capacity model | Hard |
| C8 | System uploads model for printing | **Real printer driver** (Bambu MQTT + FTPS), not a stub | **Hardest** |
| C9 | Customer sees progress in cabinet | Live telemetry → events → WebSocket → customer view | Medium |
| C9 | **Late → cheaper for the customer** | SLA clock + price decay policy + credit/refund mechanics | **Design-critical** |
| C10 | Alert personnel when print finishes | Event-driven personnel dashboard with attention queue | Medium |
| M1 | Materials table: properties, quantity, **location** (stock+shelf \| printer+AMS port), buy price /1000m, sell price /cm, status | Separate material *spec* from physical *lot*; status is a derived rollup | Medium |
| M1 | Status tags with counts above table; sortable headers; detail popup; add new | One reusable table component, configured per entity | Low (if done once) |
| M2 | Printers table: status incl. printing+ETA, amortization idle/printing, total print time, next service | Printer aggregate + telemetry rollups + amortization model | Medium |
| M3 | Printer service card: operations, periodicity, materials used | Maintenance schedule aggregate | Low |
| M4 | Table of all orders | Same table component | Low |
| M5 | **All data interconnected** | One database, one domain model, no sync layer | **Design-critical** |

### The five things that decide whether this works

1. **Pricing must be a pure, deterministic, versioned function.** C4's delta preview is impossible to do honestly any other way — you must be able to price two hypothetical specs and subtract them. Every other approach ends in the UI re-implementing arithmetic and drifting.
2. **The scheduler must exist and must be explainable.** C6/C7 are the product. Every assignment must record *why* it chose that printer.
3. **The printer driver must be real.** C8 is the whole "fully automatic" claim. This is also the highest-risk unknown and must be de-risked first, not last.
4. **Estimate-vs-actual variance must be a designed rule.** With slicing after checkout (decided), the quoted price comes from mesh heuristics and the true cost arrives later. Without an explicit tolerance policy this becomes silent margin leakage.
5. **One database.** C(M5) "all data interconnected" is not a feature, it is an architectural constraint. Mirror the domain in two stores and every field afterwards costs two entity models, two schemas and a sync mapper.

### Hidden requirement the scenario does not state

**Repeat orders must not re-pay the human slicing cost.** Since slicing is human-gated, the sliced output must be a cached, first-class artifact keyed by (model, scale, material, printer profile). First order of a model = manual prep. Every subsequent order of the same configuration = fully automatic. Without this, "human-in-the-loop" scales linearly with order volume and the farm stops being a farm.
