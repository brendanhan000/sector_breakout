import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { RRGChart } from '../src/components/RRGChart';
import { rrgResponse } from './fixtures';

describe('RRGChart', () => {
  it('renders four shaded quadrants', () => {
    render(
      <RRGChart
        data={rrgResponse()}
        loading={false}
        dimmed={false}
        onSelect={() => {}}
        highlighted={null}
      />,
    );
    for (const quadrant of ['LEADING', 'WEAKENING', 'LAGGING', 'IMPROVING']) {
      expect(screen.getByTestId(`quadrant-${quadrant}`)).toBeInTheDocument();
    }
  });

  it('draws a tail for every sector — tails are not optional', () => {
    // A static scatter gives position; the tail gives direction, and direction
    // is the trade.
    const data = rrgResponse();
    render(
      <RRGChart
        data={data}
        loading={false}
        dimmed={false}
        onSelect={() => {}}
        highlighted={null}
      />,
    );
    for (const series of data.series) {
      const tail = screen.getByTestId(`rrg-tail-${series.symbol}`);
      expect(tail).toBeInTheDocument();
      // A real polyline, not a degenerate single point.
      expect(tail.getAttribute('d')?.split('L').length).toBeGreaterThan(2);
    }
  });

  it('places each sector in the quadrant its coordinates imply', () => {
    render(
      <RRGChart
        data={rrgResponse()}
        loading={false}
        dimmed={false}
        onSelect={() => {}}
        highlighted={null}
      />,
    );
    expect(screen.getByTestId('rrg-series-XLK')).toHaveAttribute('data-quadrant', 'LEADING');
    expect(screen.getByTestId('rrg-series-XLU')).toHaveAttribute('data-quadrant', 'LAGGING');
  });

  it('selects a sector on click', async () => {
    const onSelect = vi.fn();
    render(
      <RRGChart
        data={rrgResponse()}
        loading={false}
        dimmed={false}
        onSelect={onSelect}
        highlighted={null}
      />,
    );
    await userEvent.click(screen.getByTestId('rrg-series-XLK'));
    expect(onSelect).toHaveBeenCalledWith('XLK');
  });

  it('grays itself out in a low-dispersion regime', () => {
    const { container } = render(
      <RRGChart
        data={rrgResponse()}
        loading={false}
        dimmed={true}
        onSelect={() => {}}
        highlighted={null}
      />,
    );
    expect(container.querySelector('section')?.className).toContain('opacity-40');
    expect(screen.getByText('Low dispersion — unreliable')).toBeInTheDocument();
  });

  it('measures its container instead of keeping the initial guess', () => {
    render(
      <RRGChart
        data={rrgResponse()}
        loading={false}
        dimmed={false}
        onSelect={() => {}}
        highlighted={null}
      />,
    );
    const svg = screen.getByTestId('rrg-chart');
    expect(svg.getAttribute('width')).toBe('1200');
    expect(svg.getAttribute('height')).toBe('600');
  });

  it('still measures when data arrives AFTER the first render', () => {
    // REGRESSION. The panel early-returns a placeholder while data loads, so
    // the measured element does not exist on first paint. A useEffect with an
    // empty dependency array fires against a null ref and never runs again once
    // the real element mounts, leaving the chart frozen at its initial guess —
    // too narrow to fill its panel and tall enough to overflow into the state
    // grid below. Passing data straight in (as every other test here does)
    // hides that completely.
    const props = {
      loading: false,
      dimmed: false,
      onSelect: () => {},
      highlighted: null,
    };
    const { rerender } = render(<RRGChart data={null} {...props} />);
    expect(screen.queryByTestId('rrg-chart')).not.toBeInTheDocument();

    rerender(<RRGChart data={rrgResponse()} {...props} />);

    const svg = screen.getByTestId('rrg-chart');
    expect(svg.getAttribute('width')).toBe('1200');
    expect(svg.getAttribute('height')).toBe('600');
  });

  it('centres the origin so the four quadrants are equal in area', () => {
    render(
      <RRGChart
        data={rrgResponse()}
        loading={false}
        dimmed={false}
        onSelect={() => {}}
        highlighted={null}
      />,
    );
    const leading = screen.getByTestId('quadrant-LEADING');
    const lagging = screen.getByTestId('quadrant-LAGGING');

    // A symmetric domain about the origin must put the crosshair dead centre;
    // otherwise which quadrant a point falls in would depend on where the OTHER
    // sectors happen to be.
    expect(Number(leading.getAttribute('x'))).toBeCloseTo(
      Number(leading.getAttribute('width')),
      0,
    );
    expect(Number(lagging.getAttribute('x'))).toBe(0);
    expect(Number(lagging.getAttribute('y'))).toBeCloseTo(
      Number(lagging.getAttribute('height')),
      0,
    );
  });

  it('renders a message rather than crashing without data', () => {
    render(
      <RRGChart
        data={null}
        loading={false}
        dimmed={false}
        onSelect={() => {}}
        highlighted={null}
      />,
    );
    expect(screen.getByText('No rotation data.')).toBeInTheDocument();
  });
});
