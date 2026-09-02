import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { StateGrid } from '../src/components/StateGrid';
import { planeView, sectorRow, sectorsResponse } from './fixtures';

describe('StateGrid', () => {
  it('renders every sector on both planes', () => {
    render(
      <StateGrid data={sectorsResponse()} loading={false} onSelect={() => {}} selected={null} />,
    );
    for (const symbol of ['XLK', 'XLU', 'XLE', 'XLV']) {
      expect(screen.getByTestId(`sector-row-${symbol}`)).toBeInTheDocument();
    }
  });

  it('visually emphasises rows where the two planes disagree', () => {
    // These are the actionable rows and the entire reason for two planes.
    render(
      <StateGrid data={sectorsResponse()} loading={false} onSelect={() => {}} selected={null} />,
    );

    const disagreeing = screen.getByTestId('sector-row-XLU');
    expect(disagreeing).toHaveAttribute('data-disagreement', 'true');
    expect(disagreeing.className).toContain('border-l-sig-warn');

    const agreeing = screen.getByTestId('sector-row-XLK');
    expect(agreeing).toHaveAttribute('data-disagreement', 'false');
    expect(agreeing.className).not.toContain('border-l-sig-warn');
  });

  it('counts the disagreements in the header', () => {
    render(
      <StateGrid data={sectorsResponse()} loading={false} onSelect={() => {}} selected={null} />,
    );
    expect(screen.getByTestId('disagreement-count')).toHaveTextContent('2 plane disagreements');
  });

  it('shows the three-horizon term structure, not a composite', () => {
    render(
      <StateGrid data={sectorsResponse()} loading={false} onSelect={() => {}} selected={null} />,
    );
    const row = screen.getByTestId('sector-row-XLK');
    const structures = within(row).getAllByTestId('term-structure');
    expect(structures).toHaveLength(2); // one per plane

    for (const structure of structures) {
      for (const horizon of [10, 20, 55]) {
        expect(within(structure).getByTestId(`term-dot-${horizon}`)).toBeInTheDocument();
      }
    }
  });

  it('shows bars_in_state alongside every state badge', () => {
    // A 40-day-old breakout and a 2-day-old one are different trades.
    render(
      <StateGrid data={sectorsResponse()} loading={false} onSelect={() => {}} selected={null} />,
    );
    const row = screen.getByTestId('sector-row-XLK');
    expect(within(row).getAllByTestId('state-badge').length).toBe(2);
    expect(row.textContent).toContain('4d');
  });

  it('surfaces the NARROW badge as its own element, not a tooltip', () => {
    const data = sectorsResponse({
      sectors: [
        sectorRow('XLK', 'CONFIRMED_UP', 'CONFIRMED_UP', {
          absolute: planeView({ plane: 'absolute', state: 'CONFIRMED_UP', narrow: true }),
        }),
      ],
    });
    render(<StateGrid data={data} loading={false} onSelect={() => {}} selected={null} />);
    expect(screen.getByTestId('narrow-badge')).toHaveTextContent('narrow');
  });

  it('dims the relative plane in a low-dispersion regime', () => {
    const data = sectorsResponse({ low_dispersion: true });
    render(<StateGrid data={data} loading={false} onSelect={() => {}} selected={null} />);

    const row = screen.getByTestId('sector-row-XLK');
    const cells = row.querySelectorAll('td');
    const dimmed = Array.from(cells).filter((c) => c.className.includes('opacity-40'));
    expect(dimmed.length).toBeGreaterThan(0);
  });

  it('marks quarantined sectors', () => {
    const data = sectorsResponse({
      sectors: [sectorRow('XLK', 'NEUTRAL', 'NEUTRAL', { quarantined: true })],
    });
    render(<StateGrid data={data} loading={false} onSelect={() => {}} selected={null} />);
    expect(screen.getByText('quarantined')).toBeInTheDocument();
  });

  it('selects a sector on click', async () => {
    const onSelect = vi.fn();
    render(
      <StateGrid data={sectorsResponse()} loading={false} onSelect={onSelect} selected={null} />,
    );
    await userEvent.click(screen.getByTestId('sector-row-XLE'));
    expect(onSelect).toHaveBeenCalledWith('XLE');
  });

  it('renders an empty state without data', () => {
    render(<StateGrid data={null} loading={false} onSelect={() => {}} selected={null} />);
    expect(screen.getByText('No sector data.')).toBeInTheDocument();
  });
});
