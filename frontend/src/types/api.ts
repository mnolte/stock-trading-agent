export interface SessionState {
  state: string;
  display_time: string;
  next_cycle: string | null;
}

export interface HeaderState {
  paper: boolean;
  mode: string;
  session: SessionState;
  equity_usd: string;
  equity_eur: string;
  today_usd: string;
  today_eur: string;
  regime: string;
  kill_switch: boolean;
}
