export default function Positions() {
  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>Positions & orders</h2>
      <div
        style={{
          padding: 'var(--sa-space-5)',
          background: 'var(--sa-panel)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-lg)',
          color: 'var(--sa-text-muted)',
        }}
      >
        Positions content is built in PR 5 and PR 6.
      </div>
    </div>
  );
}
