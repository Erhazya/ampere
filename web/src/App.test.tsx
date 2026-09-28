import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { exported } from './test/recent';

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

/** The API, simulated by address: the recent days, and the health it reports. */
function api(health: Response) {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>((url) =>
      Promise.resolve(url === '/api/data/recent' ? Response.json(exported()) : health),
    ),
  );
}

// Only the network is simulated here: the requests, the hooks and the screens run for real.
describe('App', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = '';
  });

  it('opens on the Data screen', async () => {
    api(Response.json({ status: 'ok', version: '0.1.0' }));
    render(<App />);

    expect(screen.getByText('Ampère')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Recent days' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Data' })).toHaveAttribute('aria-current', 'page');
    expect(await screen.findByText('Spot price, France')).toBeInTheDocument();
    expect(document.title).toBe('Recent days · Ampère');
  });

  it('shows the state of the API at #/status, then the version the API reports', async () => {
    api(Response.json({ status: 'ok', version: '0.1.0' }));
    render(<App />);
    act(() => {
      window.location.hash = '#/status';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });

    expect(screen.getByRole('heading', { level: 1, name: 'API status' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'API status' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(await screen.findByText('0.1.0')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/^Online$/);
  });

  it('says the API is not responding when the relay answers 502', async () => {
    window.location.hash = '#/status';
    api(new Response('', { status: 502, headers: { 'Content-Type': 'text/plain' } }));
    render(<App />);

    expect(await screen.findByText('The API is not responding (HTTP 502)')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/^Unavailable$/);
  });
});
