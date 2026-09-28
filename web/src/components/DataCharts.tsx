import { LineChart } from 'echarts/charts';
import { GridComponent, MarkAreaComponent, MarkLineComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { type MouseEvent, useEffect, useMemo, useRef, useState } from 'react';
import type { Recent, SeriesId } from '../api/recent';
import { clockLabel, dayLabel, momentLabel, narrowDayLabel, number } from '../format';
import {
  bands,
  type Colors,
  extent,
  latest,
  MARGIN,
  type Panel,
  PANELS,
  panelOption,
  pointAt,
  seriesOf,
} from '../panels';
import { TEXT } from '../text';
import styles from './DataCharts.module.css';

// Only what the charts draw, module by module (ADR 010): the cursor is the screen's own.
echarts.use([LineChart, GridComponent, MarkAreaComponent, MarkLineComponent, CanvasRenderer]);

const MINUTE = 60_000;
/** The step of the cursor: the quarter-hour, the step of most series. */
const QUARTER = 15 * MINUTE;

/** How far from an instant a value still counts: its series' step, or more for the weather. */
const SLACK: Partial<Record<SeriesId, number>> = {
  temperature: 45 * MINUTE,
  temperature_forecast: 45 * MINUTE,
};
const slackOf = (id: SeriesId) => SLACK[id] ?? 8 * MINUTE;

/** Below this width, in pixels, the charts leave no room beside the cursor for the card. */
const NARROW = 640;

/** The cursor: the quarter-hour under the mouse, and where the card of its values goes. */
interface Cursor {
  instant: number;
  /** The height of the mouse over the charts, in pixels. */
  y: number;
  /** In the lower half of the window, the card goes above the mouse, so that it stays seen. */
  above: boolean;
  /** On narrow charts, the card takes their whole width. */
  narrow: boolean;
}

/** The colors of the style sheet, which the canvas cannot read by itself. */
function readColors(element: Element): Colors {
  const style = getComputedStyle(element);
  const read = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  return {
    line: read('--line', '#2e2a26'),
    ink: read('--ink', '#f3efe8'),
    muted: read('--ink-muted', '#a39e96'),
    measured: read('--series-measured', '#c98500'),
    forecast: read('--series-forecast', '#3987e5'),
  };
}

/** "23:45" on the day of the visit, "Sat 26, 23:45" on another one. */
function stamp(instant: number, reference: number): string {
  const sameDay = dayLabel.format(instant) === dayLabel.format(reference);
  return sameDay
    ? clockLabel.format(instant)
    : `${dayLabel.format(instant)}, ${clockLabel.format(instant)}`;
}

function words(panel: Panel) {
  return TEXT.data.panels[panel.id];
}

/** A value of a chart in the unit on screen, with that unit. */
function amount(panel: Panel, value: number): string {
  return `${number(value * panel.scale, panel.digits)} ${words(panel).unit}`;
}

/** The header of a chart: its latest value, and what its forecast gives for the time of the
 * export, dated from the day of the visit. */
function headline(panel: Panel, recent: Recent, now: number): string {
  const at = (instant: number) => `${TEXT.data.at} ${stamp(instant, now)}`;
  const parts: string[] = [];
  const last = latest(recent.series.get(panel.measured));
  if (last) parts.push(`${amount(panel, last[1])} ${at(last[0])}`);
  if (panel.forecast) {
    const id = panel.forecast;
    const forecast = pointAt(recent.series.get(id), recent.generatedAt, slackOf(id));
    if (forecast) {
      parts.push(`${TEXT.data.series[id]} ${amount(panel, forecast[1])} ${at(forecast[0])}`);
    }
  }
  return parts.length > 0 ? parts.join(' · ') : TEXT.data.noData;
}

/**
 * The five charts, stacked on the same time axis in Paris time, with one cursor across all of
 * them (ADR 029). ECharts draws the curves; the headers, the scales, the days, the holidays, the
 * cursor and the card of values under it are HTML, so that screen readers read the text and no
 * text from a source goes through ECharts.
 */
export function DataCharts({ recent, now }: { recent: Recent; now: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const plotsRef = useRef<(HTMLDivElement | null)[]>([]);
  const [cursor, setCursor] = useState<Cursor | null>(null);
  // What the cursor does not change, computed once per export.
  const panels = useMemo(
    () =>
      PANELS.map((panel) => ({
        panel,
        scale: extent(panel, recent),
        headline: headline(panel, recent, now),
      })),
    [recent, now],
  );
  // One row for the school holidays and one for the public holidays: a public holiday often
  // falls on the first or last day of school holidays, and their names would overlap.
  const holidays = useMemo(
    () =>
      (
        [
          ['school', bands(recent.days, (day) => day.schoolHolidays)],
          ['public', bands(recent.days, (day) => day.publicHoliday)],
        ] as const
      ).filter(([, row]) => row.length > 0),
    [recent],
  );

  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    const colors = readColors(root);
    const charts: ReturnType<typeof echarts.init>[] = [];
    try {
      for (const [index, panel] of PANELS.entries()) {
        const element = plotsRef.current[index];
        if (!element) throw new Error(`no element for the chart ${panel.id}`);
        const chart = echarts.init(element, undefined, { renderer: 'canvas' });
        charts.push(chart);
        chart.setOption(panelOption(panel, recent, now, colors));
      }
    } catch (error) {
      // The charts drawn so far go too; the screen shows its message in their place.
      for (const chart of charts) chart.dispose();
      throw error;
    }
    const observer =
      typeof ResizeObserver === 'undefined'
        ? undefined
        : new ResizeObserver(() => {
            for (const chart of charts) chart.resize();
          });
    observer?.observe(root);
    return () => {
      observer?.disconnect();
      for (const chart of charts) chart.dispose();
    };
  }, [recent, now]);

  const span = recent.end - recent.start;
  // Where an instant falls across the charts, in percent of their width.
  const share = (instant: number) => ((instant - recent.start) / span) * 100;
  const place = (instant: number) => `${share(instant)}%`;

  // The quarter-hour under the mouse, within the period: the charts span the whole width.
  const follow = (event: MouseEvent<HTMLElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width <= 0) return;
    const quarters = Math.round((((event.clientX - box.left) / box.width) * span) / QUARTER);
    const instant = recent.start + quarters * QUARTER;
    setCursor({
      instant: Math.min(Math.max(instant, recent.start), recent.end - QUARTER),
      y: event.clientY - box.top,
      above: event.clientY > window.innerHeight / 2,
      narrow: box.width < NARROW,
    });
  };

  return (
    <div className={styles.charts} ref={containerRef}>
      <div
        className={styles.stack}
        onMouseMove={follow}
        onMouseLeave={() => {
          setCursor(null);
        }}
      >
        {panels.map(({ panel, scale, headline }, index) => {
          const { title, unit, bound } = words(panel);
          return (
            <section key={panel.id} className={styles.panel} aria-label={title}>
              <header className={styles.header}>
                <h2 className={styles.title}>{title}</h2>
                <span className={styles.unit}>{unit}</span>
                <span className={styles.latest}>{headline}</span>
              </header>
              <div className={styles.frame} aria-hidden="true">
                <div
                  className={styles.plot}
                  ref={(element) => {
                    plotsRef.current[index] = element;
                  }}
                />
                {scale && (
                  <>
                    <span className={`${styles.bound} ${styles.high}`}>
                      {`${number(scale[1], 0)} ${bound}`}
                    </span>
                    <span className={`${styles.bound} ${styles.low}`}>
                      {`${number(scale[0], 0)} ${bound}`}
                    </span>
                  </>
                )}
                {cursor !== null && (
                  <Marks
                    panel={panel}
                    recent={recent}
                    instant={cursor.instant}
                    scale={scale}
                    place={place}
                  />
                )}
              </div>
            </section>
          );
        })}
      </div>
      <div className={styles.axis} aria-hidden="true">
        {recent.days.map((day) => (
          <span key={day.day} className={styles.day} style={{ left: place(day.start) }}>
            <span className={styles.wide}>{dayLabel.format(day.start)}</span>
            <span className={styles.narrow}>{narrowDayLabel.format(day.start)}</span>
          </span>
        ))}
      </div>
      {holidays.map(([kind, row]) => (
        <ul key={kind} className={styles.holidays}>
          {/* A name that starts late in the period stops at the right edge, with an ellipsis. */}
          {row.map(([start, end, name]) => (
            <li
              key={`${start}-${name}`}
              className={styles.holiday}
              style={{ left: place(start), maxWidth: `${100 - share(start)}%` }}
            >
              <bdi>{name}</bdi>
              <span className={styles.hidden}>
                {` ${dayLabel.format(start)} – ${dayLabel.format(end - MINUTE)}`}
              </span>
            </li>
          ))}
        </ul>
      ))}
      {cursor !== null && (
        <CursorCard recent={recent} cursor={cursor} left={place(cursor.instant)} />
      )}
    </div>
  );
}

/** The cursor on one chart: its line, and a dot on each curve, at the point nearest to it. The
 * forecast comes first, so that the measured dot covers it, as the curves do. */
function Marks({
  panel,
  recent,
  instant,
  scale,
  place,
}: {
  panel: Panel;
  recent: Recent;
  instant: number;
  scale: [number, number] | null;
  place: (instant: number) => string;
}) {
  return (
    <>
      <span className={styles.cursor} style={{ left: place(instant) }} />
      {scale &&
        seriesOf(panel)
          .reverse()
          .map((id) => {
            const point = pointAt(recent.series.get(id), instant, slackOf(id));
            if (!point) return null;
            // The same place as ECharts gives the point, between the margins of the chart.
            const share = (scale[1] - point[1] * panel.scale) / (scale[1] - scale[0]);
            return (
              <span
                key={id}
                className={
                  id === panel.forecast ? `${styles.dot} ${styles.forecastDot}` : styles.dot
                }
                style={{
                  left: place(point[0]),
                  top: `calc(${MARGIN}px + ${share} * (100% - ${2 * MARGIN}px))`,
                }}
              />
            );
          })}
    </>
  );
}

/**
 * The values at the instant under the cursor, for a mouse: the table view serves the rest. The
 * card sits beside the cursor, on its left past 60 % of the period, at the height of the mouse.
 */
function CursorCard({ recent, cursor, left }: { recent: Recent; cursor: Cursor; left: string }) {
  const { instant, y, above, narrow } = cursor;
  const day = recent.days.find((item) => item.start <= instant && instant < item.end);
  // Only the series that have a value there.
  const rows = PANELS.flatMap((panel) =>
    seriesOf(panel).flatMap((id) => {
      const point = pointAt(recent.series.get(id), instant, slackOf(id));
      return point ? [{ id, panel, value: point[1] }] : [];
    }),
  );
  const flip = instant - recent.start > 0.6 * (recent.end - recent.start);
  const side = narrow ? styles.full : flip ? styles.flip : undefined;
  const className = [styles.card, side, above ? styles.above : undefined].filter(Boolean).join(' ');
  return (
    <div className={className} style={narrow ? { top: y } : { left, top: y }} aria-hidden="true">
      <p className={styles.moment}>{momentLabel.format(instant)}</p>
      {rows.map(({ id, panel, value }) => (
        <p key={id} className={styles.row}>
          <span className={id === panel.forecast ? styles.forecastMark : styles.measuredMark} />
          <span className={styles.name}>{TEXT.data.series[id]}</span>
          <span className={styles.value}>{amount(panel, value)}</span>
        </p>
      ))}
      {day?.publicHoliday && (
        <p className={styles.note}>
          <bdi>{day.publicHoliday}</bdi>
        </p>
      )}
      {day?.schoolHolidays && (
        <p className={styles.note}>
          <bdi>{day.schoolHolidays}</bdi>
        </p>
      )}
    </div>
  );
}
