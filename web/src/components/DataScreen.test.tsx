import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { exported } from '../test/recent';
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

describe('DataScreen', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('says it is loading, then shows the charts, the legend and the sources', async () => {
    answering(200, exported());
    render(<DataScreen />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading the recent days…');

    expect(await screen.findByText('Spot price, France')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Recent days' })).toBeInTheDocument();
    expect(screen.getByText('Measured or published')).toBeInTheDocument();
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
  });

  it('says when the recent days are unavailable', async () => {
    answering(503, { detail: 'The export of the recent days is unusable' });
    render(<DataScreen />);
    expect(await screen.findByText('The recent days are unavailable')).toBeInTheDocument();
  });
});
