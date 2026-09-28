import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DAY, exported, recent, START } from '../test/recent';
import { DataCharts } from './DataCharts';
import { ErrorBoundary } from './ErrorBoundary';

/** Monday 28 September 2026 at 11:00 in Paris, an hour after the test export was written. */
const NOW = START + 7 * DAY + 11 * 3_600_000;

// jsdom has no canvas: ECharts is replaced by fakes that keep their option.
const fake = vi.hoisted(() => {
  interface Chart {
    setOption: ReturnType<typeof vi.fn>;
    dispose: ReturnType<typeof vi.fn>;
    resize: ReturnType<typeof vi.fn>;
  }
  const charts: Chart[] = [];
  // The chart whose option throws, if any.
  const failing = { index: -1 };
  return {
    charts,
    failing,
    init: vi.fn(() => {
      const index = charts.length;
      const chart: Chart = {
        setOption: vi.fn(() => {
          if (index === failing.index) throw new Error('no canvas');
        }),
        dispose: vi.fn(),
        resize: vi.fn(),
      };
      charts.push(chart);
      return chart;
    }),
  };
});
vi.mock('echarts/core', () => ({ use: vi.fn(), init: fake.init }));
vi.mock('echarts/charts', () => ({ LineChart: {} }));
vi.mock('echarts/components', () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));

/** The stack of charts, from x = 100 and y = 50, 900 px wide by default: nine days of 100 px. */
function stack(width = 900): HTMLElement {
  const element = screen.getByRole('region', { name: 'Spot price, France' }).parentElement;
  if (!element) throw new Error('no stack of charts');
  vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
    x: 100,
    y: 50,
    left: 100,
    top: 50,
    right: 100 + width,
    bottom: 650,
    width,
    height: 600,
    toJSON: () => ({}),
  });
  return element;
}

describe('DataCharts', () => {
  beforeEach(() => {
    fake.charts.length = 0;
    fake.failing.index = -1;
    fake.init.mockClear();
  });

  it('draws five charts, each under its header, its latest value and its forecast', () => {
    render(<DataCharts recent={recent()} now={NOW} />);
    expect(fake.init).toHaveBeenCalledTimes(5);
    const titles = screen.getAllByRole('heading', { level: 2 }).map((title) => title.textContent);
    expect(titles).toEqual([
      'Spot price, France',
      'Consumption, France',
      'CO₂ intensity, France',
      'Solar output, Auvergne-Rhône-Alpes',
      'Temperature, Lyon',
    ]);
    // The day of the export gives the time alone; another day gives its name too. The forecast
    // is the one for the time of the export.
    expect(screen.getByText('175.00 €/MWh at 23:45')).toBeInTheDocument();
    expect(
      screen.getByText("45.9 GW at Sun 27, 23:45 · RTE's forecast 42.7 GW at 10:00"),
    ).toBeInTheDocument();
    expect(screen.getByText('15.5 °C at Mon 21, 00:00')).toBeInTheDocument();
    expect(screen.getByText('No value yet')).toBeInTheDocument();
  });

  it('dates the latest values from the day of the visitor', () => {
    // Tuesday 29 at 09:00: Monday 28 is no longer today.
    render(<DataCharts recent={recent()} now={START + 8 * DAY + 9 * 3_600_000} />);
    expect(screen.getByText('175.00 €/MWh at Mon 28, 23:45')).toBeInTheDocument();
    expect(
      screen.getByText("45.9 GW at Sun 27, 23:45 · RTE's forecast 42.7 GW at Mon 28, 10:00"),
    ).toBeInTheDocument();
  });

  it('lets go of the charts already drawn when one of them fails', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    fake.failing.index = 2;
    render(
      <ErrorBoundary fallback={<p>The charts failed</p>}>
        <DataCharts recent={recent()} now={NOW} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('The charts failed')).toBeInTheDocument();
    expect(fake.charts).toHaveLength(3);
    for (const chart of fake.charts) expect(chart.dispose).toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it('writes the bounds of each scale, in whole units', () => {
    render(<DataCharts recent={recent()} now={NOW} />);
    for (const bound of ['175 €', '0 €', '46 GW', '42 GW', '44 g', '0 g', '16 °C', '15 °C']) {
      expect(screen.getByText(bound)).toBeInTheDocument();
    }
    // Solar output has no value, hence no scale.
    expect(screen.queryByText(/ MW$/)).toBeNull();
  });

  it('names the nine days in Paris time', () => {
    render(<DataCharts recent={recent()} now={NOW} />);
    for (const label of ['Mon 21', 'Wed 23', 'Tue 29', 'M 21', 'T 29']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText('Vacances de test')).toBeInTheDocument();
    expect(screen.getByText('Jour de test')).toBeInTheDocument();
  });

  it('shows hostile names as text, under the charts and in the card', () => {
    const tag = '<img src=x onerror=alert(1)>';
    const redirect = '<meta http-equiv="refresh" content="0;url=https://evil.example">';
    // As the API may serve them: the dashboard checks the export first.
    const days = (exported().days as Record<string, unknown>[]).map((day, n) => ({
      ...day,
      public_holiday: n === 3 ? tag : n === 5 ? redirect : null,
      school_holidays: n === 1 || n === 2 ? 'Vacances\u202e de test' : null,
    }));
    const { container } = render(<DataCharts recent={recent({ days })} now={NOW} />);
    const charts = stack();
    // Thursday 24, Saturday 26, then Tuesday 22 under the mouse.
    for (const [clientX, name] of [
      [450, tag],
      [650, redirect],
      [250, 'Vacances de test'],
    ] as const) {
      fireEvent.mouseMove(charts, { clientX });
      expect(screen.getAllByText(name)).toHaveLength(2);
    }
    expect(container.querySelector('img, meta')).toBeNull();
    expect(container.textContent).not.toContain('\u202e');
  });

  it('writes the school holidays and the public holidays on two rows, so that they never overlap', () => {
    const data = recent();
    // The first day is both a public holiday and the last day of the school holidays, as on
    // Sunday 1 November 2026.
    const days = data.days.map((day, n) =>
      n === 0
        ? { ...day, publicHoliday: 'Toussaint', schoolHolidays: 'Vacances de la Toussaint' }
        : { ...day, publicHoliday: null, schoolHolidays: null },
    );
    render(<DataCharts recent={{ ...data, days }} now={NOW} />);
    const [school, official] = screen.getAllByRole('list');
    expect(school).toHaveTextContent(/^Vacances de la Toussaint/);
    expect(official).toHaveTextContent(/^Toussaint/);
  });

  it('keeps the name of a holiday within the charts, even on their last day', () => {
    const data = recent();
    const days = data.days.map((day, n) => ({
      ...day,
      publicHoliday: null,
      schoolHolidays: n === 8 ? 'Vacances de la Toussaint' : null,
    }));
    render(<DataCharts recent={{ ...data, days }} now={NOW} />);
    const item = screen.getByRole('listitem');
    expect(item.style.left).toBe(`${(8 / 9) * 100}%`);
    expect(item.style.maxWidth).toBe(`${100 - (8 / 9) * 100}%`);
  });

  it('leaves out the list of holidays when the period has none', () => {
    const data = recent();
    const days = data.days.map((day) => ({ ...day, publicHoliday: null, schoolHolidays: null }));
    render(<DataCharts recent={{ ...data, days }} now={NOW} />);
    expect(screen.queryByRole('list')).toBeNull();
  });

  it('shows the values of the quarter-hour under the mouse, then hides them when it leaves', () => {
    render(<DataCharts recent={recent()} now={NOW} />);
    const charts = stack();
    // 379 px of 900 are 3.79 days: Thursday 24 at 18:57, hence the quarter-hour of 19:00.
    fireEvent.mouseMove(charts, { clientX: 479 });
    expect(screen.getByText('Thu 24 Sept, 19:00')).toBeInTheDocument();
    expect(screen.getByText('156.00 €/MWh')).toBeInTheDocument();
    expect(screen.getByText('45.9 GW')).toBeInTheDocument();
    expect(screen.getByText('42.7 GW')).toBeInTheDocument();
    expect(screen.getByText('44 gCO₂/kWh')).toBeInTheDocument();
    // A series without a value there has no row.
    expect(screen.queryByText('Solar output')).toBeNull();
    expect(screen.queryByText('Temperature')).toBeNull();
    expect(screen.getAllByText('Jour de test')).toHaveLength(2);
    fireEvent.mouseLeave(charts);
    expect(screen.queryByText('Thu 24 Sept, 19:00')).toBeNull();
  });

  it('places the card at the height of the mouse, above it in the lower half of the window', () => {
    render(<DataCharts recent={recent()} now={NOW} />);
    const charts = stack();
    const card = () => screen.getByText('Thu 24 Sept, 19:00').parentElement;
    fireEvent.mouseMove(charts, { clientX: 479, clientY: 100 });
    expect(card()?.style.top).toBe('50px');
    expect(card()?.style.left).toMatch(/%$/);
    expect(card()?.className).not.toMatch(/_above_/);
    // The window of jsdom is 768 px high.
    fireEvent.mouseMove(charts, { clientX: 479, clientY: 500 });
    expect(card()?.style.top).toBe('450px');
    expect(card()?.className).toMatch(/_above_/);
    expect(card()?.className).not.toMatch(/_flip_/);
    // Monday 28, past 60 % of the period: the card goes to the left of the cursor.
    fireEvent.mouseMove(charts, { clientX: 800, clientY: 100 });
    expect(screen.getByText('Mon 28 Sept, 00:00').parentElement?.className).toMatch(/_flip_/);
  });

  it('marks the point of each curve under the cursor, on the scale of its chart', () => {
    const { container } = render(<DataCharts recent={recent()} now={NOW} />);
    fireEvent.mouseMove(stack(), { clientX: 479 });
    const dots = [...container.querySelectorAll<HTMLElement>('[class*="_dot_"]')];
    // The price, the forecast of RTE under the consumption, and the CO2 intensity.
    expect(dots).toHaveLength(4);
    expect(dots[1].className).toMatch(/_forecastDot_/);
    // 156 €/MWh on a scale from 0 to 175, at 19:00 on Thursday 24: 3.79 days of 9.
    expect(dots[0].style.left).toBe(`${(364 / 864) * 100}%`);
    expect(dots[0].getAttribute('style')).toContain(`calc(6px + ${19 / 175} * (100% - 12px))`);
  });

  it('gives the card the whole width of narrow charts', () => {
    render(<DataCharts recent={recent()} now={NOW} />);
    // A third of 300 px: three days after Monday 21.
    fireEvent.mouseMove(stack(300), { clientX: 200, clientY: 100 });
    const card = screen.getByText('Thu 24 Sept, 00:00').parentElement;
    expect(card?.style.left).toBe('');
    expect(card?.className).toMatch(/_full_/);
  });

  it('keeps the cursor within the period', () => {
    render(<DataCharts recent={recent()} now={NOW} />);
    const charts = stack();
    fireEvent.mouseMove(charts, { clientX: 2_000 });
    expect(screen.getByText('Tue 29 Sept, 23:45')).toBeInTheDocument();
    fireEvent.mouseMove(charts, { clientX: 0 });
    expect(screen.getByText('Mon 21 Sept, 00:00')).toBeInTheDocument();
  });

  it('lets the charts go with the screen', () => {
    const { unmount } = render(<DataCharts recent={recent()} now={NOW} />);
    unmount();
    for (const chart of fake.charts) expect(chart.dispose).toHaveBeenCalled();
  });
});
