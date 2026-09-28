import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';

function Broken(): never {
  throw new Error('broken');
}

describe('ErrorBoundary', () => {
  it('shows its fallback instead of children that throw', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(
      <ErrorBoundary fallback={<p>The screen failed</p>}>
        <Broken />
      </ErrorBoundary>,
    );
    expect(screen.getByText('The screen failed')).toBeInTheDocument();
    vi.restoreAllMocks();
  });
});
