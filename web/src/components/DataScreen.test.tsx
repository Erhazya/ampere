import { fireEvent, render, screen, within } from '@testing-library/react';
import * as echarts from 'echarts/core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DAY, exported, START } from '../test/recent';
import { DataScreen } from './DataScreen';

// jsdom has no canvas: each chart is a fake that accepts everything.
vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}));
vi.mock('echarts/charts', () => ({ LineChart: {} }));
vi.mock('echarts/components', () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));

const answering = (status: number, body: unknown) => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(Response.json(body, { status })));
};

const HOUR = 3_600_000;

/** The words of the legend, item by item. */
function legendItems(): (string | null)[] {
  const legend = screen.getByText('Measured or published').closest('ul');
  if (!legend) throw new Error('no legend');
  return within(legend)
    .getAllByRole('listitem')
    .map((item) => item.textContent);
}

describe('DataScreen', () => {
  beforeEach(() => {
    // Monday 28 September 2026 at 11:00 in Paris, an hour after the test export was written.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(START + 7 * DAY + 11 * HOUR);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('says it is loading, then shows the charts, the legend and the sources', async () => {
    answering(200, exported());
    render(<DataScreen />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading the recent days…');

    expect(await screen.findByText('Spot price, France')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Recent days' })).toBeInTheDocument();
    expect(legendItems()).toEqual(['Measured or published', 'Forecast', 'Tomorrow', 'Now']);
    expect(screen.queryByText(/has not updated these days/)).toBeNull();
    const sources = screen.getByRole('contentinfo');
    // Names and licences from the code; the date each source published, when it asks for it.
    expect(within(sources).getByText(/RTE, éCO2mix/)).toHaveTextContent(
      'RTE, éCO2mix · Licence Ouverte 2.0 · updated 28 Sept 2026 · received Mon 28 Sept, 09:06',
    );
    expect(
      within(sources).getAllByRole('link', { name: 'Licence Ouverte 2.0' })[0],
    ).toHaveAttribute('href', 'https://www.etalab.gouv.fr/licence-ouverte-open-licence/');
    expect(within(sources).getByText(/Bundesnetzagentur/)).toHaveTextContent(
      'Bundesnetzagentur | SMARD.de · CC BY 4.0 · received Sun 27 Sept, 00:00',
    );
    // A date the export lacks is said to be missing, and the Licence Ouverte asks for this one.
    expect(within(sources).getByText(/Open-Meteo/)).toHaveTextContent(
      'Weather data by Open-Meteo.com · CC BY 4.0 · nothing received for these days',
    );
    expect(within(sources).getByText(/Etalab/)).toHaveTextContent(
      'Etalab, jours fériés · Licence Ouverte 2.0 · update date unknown · nothing received for these days',
    );
  });

  it('shows the same days as a table, hour by hour', async () => {
    answering(200, exported());
    render(<DataScreen />);
    await screen.findByText('Spot price, France');
    fireEvent.click(screen.getByRole('button', { name: 'Table' }));

    expect(screen.getByRole('button', { name: 'Table' })).toHaveAttribute('aria-pressed', 'true');
    const table = screen.getByRole('table');
    expect(within(table).getByRole('columnheader', { name: /RTE's forecast/ })).toBeInTheDocument();
    const first = within(table).getAllByRole('row')[1];
    expect(within(first).getByRole('rowheader')).toHaveTextContent('Mon 21 Sept, 00:00');
    expect(within(first).getAllByRole('cell')[0]).toHaveTextContent('80.00');
  });

  it('says when there is no export yet', async () => {
    answering(503, { detail: 'No export of the recent days yet' });
    render(<DataScreen />);
    expect(await screen.findByText('No data yet')).toBeInTheDocument();
    // Nothing changes before the next daily job: there is nothing to try again.
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('says why the recent days are unavailable, and asks again on demand', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(new Response('', { status: 502 }))
        .mockResolvedValue(Response.json(exported())),
    );
    render(<DataScreen />);
    expect(await screen.findByText('The recent days are unavailable')).toBeInTheDocument();
    expect(screen.getByText('The API is not responding (HTTP 502).')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Spot price, France')).toBeInTheDocument();
  });

  it('warns when the export is more than a day old, and leaves now out of the legend', async () => {
    vi.setSystemTime(START + 10 * DAY);
    answering(200, exported());
    render(<DataScreen />);
    expect(
      await screen.findByText(
        'The daily job has not updated these days since the export of Mon 28 Sept, 10:00.',
      ),
    ).toBeInTheDocument();
    expect(legendItems()).toEqual(['Measured or published', 'Forecast', 'Tomorrow']);
  });

  it('keeps the rest of the screen when the charts fail', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(echarts.init).mockImplementationOnce(() => {
      throw new Error('no canvas');
    });
    answering(200, exported());
    render(<DataScreen />);
    expect(await screen.findByText('The charts could not load')).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Table' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
  });
});
