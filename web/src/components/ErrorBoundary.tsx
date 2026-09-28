import { Component, type ReactNode } from 'react';

/**
 * Shows `fallback` instead of its children when they throw, while rendering or in an effect, so
 * that the rest of the page stays: without it, React removes the whole page. React itself writes
 * the error in the browser's console.
 */
export class ErrorBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
