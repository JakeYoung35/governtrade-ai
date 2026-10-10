"""Replace these simulation adapters when integrating a real market data / broker provider."""
class SimulatedBroker:
    def fill(self,amount,price):
        fee=round(amount*.01,2)
        return {'fee':fee,'principal':round(amount-fee,2),'quantity':round(amount-fee,2)/price,'price':price}
    def settlement(self,market_id,direction,quantity):
        won=(sum(map(ord,market_id))%2==0)==(direction=='Yes')
        return {'won':won,'payout':round(quantity if won else 0,2)}
class SimulatedMarketData:
    def quote(self,market):
        return {'price':market['price'],'asOf':market['quoteAt'],'slippage':market['slippage'],'closeAt':market['closeAt']}
BROKER=SimulatedBroker()
MARKET_DATA=SimulatedMarketData()
