# Ampère

A digital twin of a 200-house neighbourhood's energy, fed with real public French data.

> **Status: work in progress.** The design is done and the foundations are being built. There is no demo yet.

[Version française](README.fr.md)

## What it will do

Ampère simulates a fictional neighbourhood of 200 houses near Lyon, France. Everything around it is real: the weather, electricity spot prices, the carbon intensity of the grid and the average consumption of households in the region. Every day, it will:

- forecast the neighbourhood's consumption for the next day;
- compare battery strategies on the electricity bill and CO₂ emissions: no battery, a simple rule, and mathematical optimisation (MILP);
- show the results on an interactive dashboard, with what-if scenarios.

All results will be measured on one full year of test data, from July 2025 to June 2026, never used during development.

## Key design choices

| Choice | Why |
|---|---|
| Real residential load curves from Enedis open data | Forecasts are scored against real measurements, not synthetic data |
| Dynamic spot-price tariff, from SMARD | With a flat price, a simple rule is already almost optimal, so optimisation would have nothing to show |
| 15-minute simulation step, forecasts scored on 30-minute sums | Follows the market without scoring invented detail |
| One year of test data, locked away | Honest evaluation across all seasons |

The full reasoning is in the design document and the architecture decision records, written in French during development: [design](docs/conception.md), [decisions](docs/decisions/), [glossary](docs/glossaire.md).

## Roadmap

| Step | Content | Status |
|---|---|---|
| 0 | Design | Done |
| 1 | Foundations: repository, CI, deployed skeleton | In progress |
| 2 | Data: daily ingestion and quality checks | To do |
| 3 | Simulation: houses, solar panels, batteries | To do |
| 4 | Control: simple rule vs MILP | To do |
| 5 | Forecasts: naive baselines vs LightGBM | To do |
| 6 | Dashboard and first public release | To do |

## Data sources

| Source | Used for | Licence |
|---|---|---|
| RTE éCO2mix | Grid carbon intensity, regional solar output | Licence Ouverte 2.0 |
| Enedis open data | Residential consumption, small solar installations | Licence Ouverte 2.0 |
| Weather data by Open-Meteo.com | Observed and forecast weather | CC BY 4.0 |
| PVGIS, European Commission (JRC) | Calibration of the solar model | Reuse authorised with acknowledgement |
| Bundesnetzagentur, SMARD.de | Day-ahead spot prices for France | CC BY 4.0 |
| Etalab, French Ministry of Education | Public holidays, school holidays | Licence Ouverte |

## Licence

The code is released under the MIT licence. The data remain under their own licences, listed above.
