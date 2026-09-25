// Runs before each test file: DOM matchers such as toBeInTheDocument(),
// and an empty page again after each test.
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Tells React it runs under tests, so an update made outside act() is reported. Testing
// Library only sets this itself when test globals are on, which they are not here.
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
});
