// Whether the workspace can trade, with the same conditions as the trading path: a connected (tested) Kalshi account,
// and an agent on it with a validated model and a saved mandate. Rules are already set (RulesSetup gates the app).
export function readiness(workspace) {
  const connected = (workspace.accounts || []).filter(a => a.status === 'connected');
  const onConnected = new Set(connected.map(a => a.id));
  const agents = (workspace.agentConfigs || []).filter(a => a.status !== 'deleted');
  const trading = agents.find(a => a.modelValidated && a.mandate && onConnected.has(a.accountId));
  const needsMandate = agents.find(a => a.modelValidated && !a.mandate);
  const waiting = !trading && agents.find(a => a.modelValidated && a.mandate);  // set up, but its account is not connected
  const troubled = (workspace.accounts || []).find(a => a.status === 'error' || (a.status === 'disconnected' && a.hasCredentials));
  return {connected: connected.length > 0, trading: !!trading, needsMandate, waiting: waiting || null, troubled, ready: connected.length > 0 && !!trading};
}
