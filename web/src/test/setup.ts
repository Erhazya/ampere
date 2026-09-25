// Runs before each test file: DOM matchers such as toBeInTheDocument(),
// and an empty page again after each test.
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
});
