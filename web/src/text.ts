import { REFRESH_MS, TIMEOUT_MS } from './api/config';
import type { Failure } from './api/health';

/** Words of the interface, in one place: English first, French later (ADR 010). */
export const TEXT = {
  tagline: 'Energy digital twin',
  screens: { label: 'Screens', data: 'Data', status: 'API status' },
  statusTitle: 'API status',
  statusIntro: `The dashboard asks the API again ${REFRESH_MS / 1000} seconds after each answer.`,
  states: { checking: 'Checking…', up: 'Online', down: 'Unavailable' },
  labels: { version: 'Version', lastCheck: 'Last check', endpoint: 'Endpoint', reason: 'Reason' },
  data: {
    title: 'Recent days',
    intro:
      "Seven days back in Paris time, today, and what is already known of tomorrow: the spot price, RTE's forecast and the weather forecast. The daily job updates them every day after 14:00.",
    views: { label: 'View', chart: 'Chart', table: 'Table' },
    legend: {
      measured: 'Measured or published',
      forecast: 'Forecast',
      tomorrow: 'Tomorrow',
      now: 'Now',
    },
    // The unit next to the title and the values; the shorter one on the bounds of the scale.
    panels: {
      price: { title: 'Spot price, France', unit: '€/MWh', bound: '€' },
      consumption: { title: 'Consumption, France', unit: 'GW', bound: 'GW' },
      co2: { title: 'CO₂ intensity, France', unit: 'gCO₂/kWh', bound: 'g' },
      solar: { title: 'Solar output, Auvergne-Rhône-Alpes', unit: 'MW', bound: 'MW' },
      temperature: { title: 'Temperature, Lyon', unit: '°C', bound: '°C' },
    },
    at: 'at',
    series: {
      price: 'Spot price',
      consumption: 'Consumption',
      rte_forecast: "RTE's forecast",
      co2: 'CO₂ intensity',
      solar: 'Solar output',
      temperature: 'Temperature',
      temperature_forecast: 'Forecast temperature',
    },
    noValue: '—',
    noData: 'No value yet',
    loading: {
      title: 'Loading the recent days…',
      detail: 'The charts appear as soon as the API answers.',
    },
    none: {
      title: 'No data yet',
      detail:
        'The daily job writes the recent days every day after 14:00, Paris time. They appear here after its first run.',
    },
    unavailable: {
      title: 'The recent days are unavailable',
      detail:
        'The server could not give its last export. The API status screen tells whether the API answers.',
    },
    sources: 'Sources',
    updated: 'updated',
    received: 'received',
    table: 'The recent days, hour by hour, in Paris time',
    time: 'Time',
    footer: 'Times in Paris time.',
    exportOf: 'Export of',
  },
  sources: {
    smard: { name: 'Bundesnetzagentur | SMARD.de', licence: 'CC BY 4.0' },
    eco2mix: { name: 'RTE, éCO2mix', licence: 'Licence Ouverte 2.0' },
    openmeteo: { name: 'Weather data by Open-Meteo.com', licence: 'CC BY 4.0' },
    'school-holidays': {
      name: 'Éducation nationale, calendrier scolaire',
      licence: 'Licence Ouverte 2.0',
    },
    'public-holidays': { name: 'Etalab, jours fériés', licence: 'Licence Ouverte 2.0' },
  },
  licences: {
    'CC BY 4.0': 'https://creativecommons.org/licenses/by/4.0/',
    'Licence Ouverte 2.0': 'https://www.etalab.gouv.fr/licence-ouverte-open-licence/',
  },
} as const;

/** Why a check failed, in plain words rather than the browser's own messages. */
export function reasonText(failure: Failure): string {
  switch (failure.kind) {
    case 'timeout':
      return `No answer within ${TIMEOUT_MS / 1000} s`;
    case 'network':
      // fetch cannot tell the user's network from the dashboard's own server being down.
      return 'Cannot reach the server';
    case 'http':
      // 502, 503 and 504 come from the relay in front of the API when the API does not answer.
      return [502, 503, 504].includes(failure.status)
        ? `The API is not responding (HTTP ${failure.status})`
        : `The API returned an error (HTTP ${failure.status})`;
    case 'format':
      return 'Unexpected answer from the API';
    case 'unexpected':
      return 'Unexpected error, see the browser console';
  }
}
