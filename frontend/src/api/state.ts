import { get } from './client';
import type { HeaderState } from '../types/api';

export function getHeaderState(): Promise<HeaderState> {
  return get<HeaderState>('/api/header');
}
