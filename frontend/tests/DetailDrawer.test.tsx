import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { DetailDrawer } from '../src/components/DetailDrawer';
import { historyResponse } from './fixtures';

describe('DetailDrawer', () => {
  it('renders nothing until a sector is selected', () => {
    const { container } = render(
      <DetailDrawer
        symbol={null}
        history={null}
        loading={false}
        plane="absolute"
        onPlaneChange={() => {}}
        onClose={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows all four panels for the selected sector', () => {
    render(
      <DetailDrawer
        symbol="XLK"
        history={historyResponse()}
        loading={false}
        plane="absolute"
        onPlaneChange={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId('detail-drawer')).toBeInTheDocument();
    expect(screen.getByText(/Donchian channels/)).toBeInTheDocument();
    expect(screen.getByText(/Residual series/)).toBeInTheDocument();
    expect(screen.getByText('Signal term structure')).toBeInTheDocument();
    expect(screen.getByText('State transitions')).toBeInTheDocument();
  });

  it('lists past FAILED_* events in the transition history', () => {
    // Failed breakouts are the highest-quality reversal signal in the system,
    // so they must remain visible after the state has cooled back to neutral.
    render(
      <DetailDrawer
        symbol="XLK"
        history={historyResponse()}
        loading={false}
        plane="absolute"
        onPlaneChange={() => {}}
        onClose={() => {}}
      />,
    );
    const row = screen.getByTestId('transition-2024-04-02');
    expect(row).toHaveTextContent('FAILED UP');
    expect(row).toHaveTextContent('CONFIRMED UP'); // the from_state
  });

  it('switches planes', async () => {
    const onPlaneChange = vi.fn();
    render(
      <DetailDrawer
        symbol="XLK"
        history={historyResponse()}
        loading={false}
        plane="absolute"
        onPlaneChange={onPlaneChange}
        onClose={() => {}}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'relative' }));
    expect(onPlaneChange).toHaveBeenCalledWith('relative');
  });

  it('closes', async () => {
    const onClose = vi.fn();
    render(
      <DetailDrawer
        symbol="XLK"
        history={historyResponse()}
        loading={false}
        plane="absolute"
        onPlaneChange={() => {}}
        onClose={onClose}
      />,
    );
    await userEvent.click(screen.getByLabelText('Close detail'));
    expect(onClose).toHaveBeenCalled();
  });

  it('toggles the channel overlays', async () => {
    render(
      <DetailDrawer
        symbol="XLK"
        history={historyResponse()}
        loading={false}
        plane="absolute"
        onPlaneChange={() => {}}
        onClose={() => {}}
      />,
    );
    const toggle = screen.getByRole('button', { name: /hide channels/i });
    await userEvent.click(toggle);
    expect(screen.getByRole('button', { name: /show channels/i })).toBeInTheDocument();
  });
});
