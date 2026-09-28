import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { recent } from '../test/recent';
import { DataTable } from './DataTable';

describe('DataTable', () => {
  it('says so when no series has a value', () => {
    render(<DataTable recent={recent({ series: [] })} />);
    expect(screen.getByRole('cell', { name: 'No value yet' })).toBeInTheDocument();
  });
});
