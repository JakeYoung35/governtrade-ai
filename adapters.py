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

# Authenticated venue implementations. No provider response body is exposed on errors.
import base64, datetime, json, math, secrets, time
from abc import ABC, abstractmethod
from decimal import Decimal
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote, urlsplit
from urllib.error import HTTPError, URLError

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
class VenueError(ValueError):pass
class VenueAdapter(ABC):
    def __init__(self, credentials, environment='sandbox'):
        if environment not in ('sandbox','live'):raise VenueError('Invalid environment.')
        self.credentials=credentials;self.environment=environment
    def request(self,method,path,body=None,public=False):
        raw=json.dumps(body).encode() if body is not None else None
        url=self.base+path;headers={'Content-Type':'application/json'}
        if not public:headers.update(self.headers(method,url))
        try:
            with urlopen(Request(url,data=raw,method=method,headers=headers),timeout=15) as r:return json.load(r)
        except HTTPError as e:raise VenueError(f'{self.venue} returned HTTP {e.code}. Check credentials, environment and permissions.') from None
        except (URLError,TimeoutError,ValueError):raise VenueError(f'{self.venue} request failed. No confirmed execution result.') from None
    def test_connection(self):return self.get_balance_positions()
    @abstractmethod
    def get_markets(self,category):pass
    @abstractmethod
    def get_quote(self,market_id,direction):pass
    @abstractmethod
    def get_balance_positions(self):pass
    @abstractmethod
    def place_order(self,order):pass
    @abstractmethod
    def cancel_orders(self,ids):pass
    def pages(self,path,key):
        rows=[];cursor=None
        for _ in range(100):
            response=self.request('GET',path+('&' if '?' in path else '?')+urlencode({'cursor':cursor} if cursor else {}))
            rows+=response.get(key,[]);cursor=response.get('cursor')
            if not cursor:return rows
        raise VenueError('Account pagination exceeded the supported limit; balances cannot be safely evaluated.')

class KalshiAdapter(VenueAdapter):
    venue='kalshi';categories=['crypto','sports','economics','politics','culture','other']
    def __init__(self,credentials,environment='sandbox',subaccount=0):
        super().__init__(credentials,environment)
        if type(subaccount)!=int or not 0<=subaccount<=63:raise VenueError('Kalshi subaccount must be 0–63.')
        self.subaccount=subaccount
        self.base=('https://external-api.demo.kalshi.co' if environment=='sandbox' else 'https://external-api.kalshi.com')+'/trade-api/v2'
    def headers(self,method,url):
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import padding,rsa
        try:
            key=serialization.load_pem_private_key(self.credentials['private_key'].encode(),password=None)
            if not isinstance(key,rsa.RSAPrivateKey):raise ValueError()
            timestamp=str(int(time.time()*1000));message=(timestamp+method+urlsplit(url).path).encode()
            signature=key.sign(message,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=padding.PSS.DIGEST_LENGTH),hashes.SHA256())
            return {'KALSHI-ACCESS-KEY':self.credentials['key_id'],'KALSHI-ACCESS-TIMESTAMP':timestamp,'KALSHI-ACCESS-SIGNATURE':base64.b64encode(signature).decode()}
        except (ValueError,KeyError,TypeError):raise VenueError('Kalshi requires an API key ID and an unencrypted RSA PEM private key.') from None
    def get_markets(self,category):
        return self.request('GET','/markets?status=open&limit=100',public=True).get('markets',[])
    def get_quote(self,market_id,direction):
        m=self.request('GET','/markets/'+quote(market_id,safe=''),public=True)['market']
        event=self.request('GET','/events/'+quote(m['event_ticker'],safe=''),public=True)['event']
        category={'financials':'economics','economics':'economics','politics':'politics','sports':'sports','crypto':'crypto','entertainment':'culture','mentions':'culture'}.get(event.get('category','').lower(),'other')
        series=self.request('GET','/series/'+quote(event['series_ticker'],safe=''),public=True)['series']
        fee_type=event.get('fee_type_override') or series['fee_type'];multiplier=event.get('fee_multiplier_override')
        multiplier=float(series['fee_multiplier'] if multiplier is None else multiplier)
        price=float(m.get('yes_ask_dollars' if direction=='Yes' else 'no_ask_dollars',0))
        return {'id':market_id,'name':m.get('title',market_id),'agent':category,'instrument':'prediction','venue':self.venue,'price':price,'quoteAt':now(),'closeAt':m.get('close_time',now()),'slippage':0,'open':m.get('status')=='active','direction':direction,'executionReady':m.get('market_type')=='binary' and float(m.get('notional_value_dollars',1))==1 and fee_type.startswith('quadratic') and math.isfinite(multiplier) and 0<=multiplier<=100 and 0<price<1,'feeMultiplier':multiplier,'executionNote':'Only binary quadratic-fee markets with a valid ask are supported.'}
    def get_balance_positions(self):
        scope='subaccount='+str(self.subaccount)
        b=self.request('GET','/portfolio/balance?'+scope)
        # The requested balance fields are cents, not fixed-point dollar strings.
        cash=float(b['balance'])/100;value=float(b['portfolio_value'])/100
        positions=self.pages('/portfolio/positions?limit=100&'+scope,'market_positions')
        settlements=self.pages('/portfolio/settlements?limit=100&'+scope,'settlements')
        fills=self.pages('/portfolio/fills?limit=100&'+scope,'fills')
        try:
            resting=self.request('GET','/portfolio/summary/total_resting_order_value')
            reserved=float(resting['total_resting_order_value'])/100
            reserved_source='Reserved cash reported by Kalshi'
            if self.subaccount!=0:raise VenueError('Summary is not scoped to a subaccount.')
        except VenueError:
            # Official summary is FCM-only. For normal members/subaccounts,
            # derive scoped outstanding collateral from paginated resting orders.
            orders=self.pages('/portfolio/orders?status=resting&limit=100&'+scope,'orders')
            reserved=0.
            for o in orders:
                quantity=float(o['remaining_count_fp']);side=o.get('side') or ('yes' if o.get('book_side')=='bid' else 'no')
                price=float(o['yes_price_dollars'] if side=='yes' else o['no_price_dollars'])
                reserved+=quantity*price
            reserved_source='Reserved cash calculated from your open orders'
        settled={x['ticker'] for x in settlements}
        # Position realized P/L includes trading closes; settlement rows provide
        # missing settlement outcomes. Never add both records for the same ticker.
        realized=0.;exposure=0.;covered=set()
        fee_by_market={}
        for f in fills:fee_by_market[f['ticker']]=fee_by_market.get(f['ticker'],0)+float(f['fee_cost'])
        open_cost=0.
        for pos in positions:
            ticker=pos['ticker'];covered.add(ticker)
            realized+=float(pos['realized_pnl_dollars'])-float(pos.get('fees_paid_dollars',fee_by_market.get(ticker,0)))
            if float(pos['position_fp'])!=0 and ticker not in settled:
                exposure+=float(pos['market_exposure_dollars']);open_cost+=float(pos['market_exposure_dollars'])
                # Fees are expensed in realized P/L when incurred, not again in open P/L.
        for settlement in settlements:
            if settlement['ticker'] not in covered:
                realized+=float(settlement['revenue'])/100-float(settlement['yes_total_cost_dollars'])-float(settlement['no_total_cost_dollars'])-float(settlement['fee_cost'])
        today=now()[:10]
        daily=sum(float(f['count_fp'])*max(float(f['yes_price_dollars']),float(f['no_price_dollars']))+float(f['fee_cost']) for f in fills if (f.get('created_time') or datetime.datetime.fromtimestamp(f.get('ts',0),datetime.timezone.utc).isoformat())[:10]==today)
        return {'externalDailySpend':daily,'cash':cash,'reserved':reserved,'reservedSource':reserved_source,'positionValue':value,'exposure':exposure,'realizedPnl':round(realized,4),'unrealizedPnl':round(value-open_cost,4),'positions':positions,'asOf':now(),'source':'Kalshi Demo' if self.environment=='sandbox' else 'Kalshi Live','mock':False,'subaccount':self.subaccount,'pnlMethod':'Venue realized positions; settlement fallback without duplicate tickers; marked open cost; trading fees expensed in realized P/L from positions/fills.'}
    def place_order(self,order):
        m=order['market'];price=Decimal(str(m['price']));budget=Decimal(str(order['amount']))
        # Worst-case quadratic taker fee occurs at 0.5. Round UP per contract,
        # plus one cent per contract for balance rounding. Never rely on price improvement.
        fee=Decimal(str(math.ceil(.0175*m['feeMultiplier']*100)/100+.01))
        count=int(budget/(price+fee))
        if count<1:raise VenueError('Budget cannot cover one contract and conservative fee reserve.')
        yes_price=price if order['direction']=='Yes' else Decimal('1')-price
        response=self.request('POST','/portfolio/events/orders',{'ticker':m['id'],'client_order_id':order['id'],'side':'bid' if order['direction']=='Yes' else 'ask','count':str(count),'price':str(yes_price),'time_in_force':'immediate_or_cancel','self_trade_prevention_type':'taker_at_cross','cancel_order_on_pause':True,'subaccount':self.subaccount})
        return response
    def cancel_orders(self,ids):
        return [self.request('DELETE','/portfolio/events/orders/'+quote(i,safe='')) for i in ids]

ADAPTERS={'kalshi':KalshiAdapter}
def adapter(venue,credentials,environment,subaccount=0):
    if venue not in ADAPTERS:raise VenueError('Only Kalshi connections are supported.')
    return ADAPTERS[venue](credentials,environment,subaccount)
