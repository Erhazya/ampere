import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

// A screen that throws as it renders.
vi.mock('./components/StatusScreen', () => ({
  StatusScreen: () => {
    throw new Error('broken');
  },
}));

describe('App, when a screen fails', () => {
  afterEach(() => {
    window.location.hash = '';
    vi.restoreAllMocks();
  });

  it('keeps the bar at the top, with the way to the other screen', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    window.location.hash = '#/status';
    render(<App />);
    expect(screen.getByText('This screen stopped on an error')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Data' })).toHaveAttribute('href', '#/');
  });
});
