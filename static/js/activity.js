export function filterOrders(orders, filters) {
  const query = (filters.query || '').toLowerCase();
  return orders.filter(o => (!filters.status || o.status===filters.status) && (!filters.agent || o.agent===filters.agent) && (!filters.category || o.market?.agent===filters.category) && (!filters.decision || o.decision===filters.decision) && JSON.stringify(o).toLowerCase().includes(query));
}
export function dailyOutcomes(orders) {
  const days = new Map();
  for (const o of orders) {const day=(o.time || '').slice(0,10); if(!day) continue; if(!days.has(day)) days.set(day,{ALLOW:0,REQUIRE_APPROVAL:0,BLOCK:0}); if(o.decision in days.get(day)) days.get(day)[o.decision]++;}
  return [...days.entries()].sort(([a],[b])=>a.localeCompare(b)).slice(-7);
}
