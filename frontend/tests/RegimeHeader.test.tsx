import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { RegimeHeader } from '../src/components/RegimeHeader';
import { regimeResponse } from './fixtures';

describe('RegimeHeader', () => {
  it('shows the benchmark state, dispersion, correlation and as_of', () => {
    render(
      <RegimeHeader
        regime={regimeResponse()}
        loading={false}
        refreshing={false}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText('SPY')).toBeInTheDocument();
    expect(screen.getByText('CONFIRMED UP')).toBeInTheDocument();
    expect(screen.getByText('42d')).toBeInTheDocument();
    expect(screen.getByText('63%')).toBeInTheDocument(); // dispersion percentile
    expect(screen.getByText('0.68')).toBeInTheDocument(); // correlation
    expect(screen.getByText('2024-06-28')).toBeInTheDocument();
    expect(screen.getByTestId('sparkline')).toBeInTheDocument();
  });

  it('shows no low-dispersion banner in a normal regime', () => {
    render(
      <RegimeHeader
        regime={regimeResponse()}
        loading={false}
        refreshing={false}
        onRefresh={() => {}}
      />,
    );
    expect(screen.queryByTestId('low-dispersion-banner')).not.toBeInTheDocument();
  });

  it('warns loudly when dispersion is in the bottom quartile', () => {
    // Not decoration: in this regime every relative-plane signal is noise.
    render(
      <RegimeHeader
        regime={regimeResponse({
          low_dispersion: true,
          dispersion_percentile: 11.2,
          message: 'Low dispersion — rotation signals unreliable.',
        })}
        loading={false}
        refreshing={false}
        onRefresh={() => {}}
      />,
    );
    const banner = screen.getByTestId('low-dispersion-banner');
    expect(banner).toHaveTextContent('Low dispersion — rotation signals unreliable.');
    expect(banner).toHaveTextContent('11%');
  });

  it('surfaces the data quality flag', () => {
    render(
      <RegimeHeader
        regime={regimeResponse({ data_quality: 'DEGRADED' })}
        loading={false}
        refreshing={false}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText('DEGRADED')).toBeInTheDocument();
  });

  it('renders placeholders while loading rather than fake zeros', () => {
    render(
      <RegimeHeader regime={null} loading={true} refreshing={false} onRefresh={() => {}} />,
    );
    expect(screen.getAllByText('…').length).toBeGreaterThan(0);
  });
});
