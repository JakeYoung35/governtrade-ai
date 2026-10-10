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
from decimal import Decimal, ROUND_DOWN
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
    def __init__(self,credentials,environment='sandbox'):
        super().__init__(credentials,environment)
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
        return {'id':market_id,'name':m.get('title',market_id),'agent':category,'instrument':'prediction','venue':self.venue,'price':price,'quoteAt':now(),'closeAt':m.get('close_time',now()),'slippage':0,'open':m.get('status')=='active','direction':direction,'executionReady':fee_type.startswith('quadratic') and math.isfinite(multiplier) and 0<=multiplier<=100 and 0<price<1,'feeMultiplier':multiplier,'executionNote':'Only binary quadratic-fee markets with a valid ask are supported.'}
    def get_balance_positions(self):
        b=self.request('GET','/portfolio/balance');positions=self.pages('/portfolio/positions?limit=100','market_positions')
        cash=float(b['balance_dollars']) if 'balance_dollars' in b else b['balance']/100;value=b['portfolio_value']/100
        realized=sum(float(p.get('realized_pnl_dollars',0)) for p in positions)
        exposure=sum(float(p.get('market_exposure_dollars',0)) for p in positions)
        midnight=int(datetime.datetime.now(datetime.timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
        fills=self.pages('/portfolio/fills?limit=100&min_ts='+str(midnight),'fills')
        daily=sum(float(f['count_fp'])*max(float(f['yes_price_dollars']),float(f['no_price_dollars']))+float(f['fee_cost']) for f in fills)
        return {'externalDailySpend':daily,'cash':cash,'positionValue':value,'exposure':exposure,'realizedPnl':realized,'unrealizedPnl':value-exposure,'positions':positions,'asOf':now(),'source':'Kalshi demo exchange' if self.environment=='sandbox' else 'Kalshi live account','mock':False}
    def place_order(self,order):
        m=order['market'];price=Decimal(str(m['price']));budget=Decimal(str(order['amount']))
        # Worst-case quadratic taker fee occurs at 0.5. Round UP per contract,
        # plus one cent per contract for balance rounding. Never rely on price improvement.
        fee=Decimal(str(math.ceil(.0175*m['feeMultiplier']*100)/100+.01))
        count=int(budget/(price+fee))
        if count<1:raise VenueError('Budget cannot cover one contract and conservative fee reserve.')
        yes_price=price if order['direction']=='Yes' else Decimal('1')-price
        response=self.request('POST','/portfolio/events/orders',{'ticker':m['id'],'client_order_id':order['id'],'side':'bid' if order['direction']=='Yes' else 'ask','count':str(count),'price':str(yes_price),'time_in_force':'immediate_or_cancel','self_trade_prevention_type':'taker_at_cross','cancel_order_on_pause':True})
        return response
    def cancel_orders(self,ids):
        return [self.request('DELETE','/portfolio/events/orders/'+quote(i,safe='')) for i in ids]

class CoinbaseAdapter(VenueAdapter):
    venue='coinbase';categories=['crypto']
    def __init__(self,credentials,environment='sandbox'):
        super().__init__(credentials,environment)
        self.base=('https://api-sandbox.coinbase.com' if environment=='sandbox' else 'https://api.coinbase.com')+'/api/v3/brokerage'
    def headers(self,method,url):
        if self.environment=='sandbox':return {} # Official static sandbox does not authenticate.
        import jwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        try:
            key=serialization.load_pem_private_key(self.credentials['private_key'].encode(),password=None)
            if not isinstance(key,ec.EllipticCurvePrivateKey) or not isinstance(key.curve,ec.SECP256R1):raise ValueError()
            t=int(time.time());name=self.credentials['key_name'];u=urlsplit(url)
            token=jwt.encode({'sub':name,'iss':'cdp','nbf':t,'exp':t+120,'uri':method+' '+u.netloc+u.path},key,algorithm='ES256',headers={'kid':name,'nonce':secrets.token_hex()})
            return {'Authorization':'Bearer '+token}
        except (ValueError,KeyError,TypeError):raise VenueError('Coinbase requires the CDP key name and an ECDSA P-256 PEM private key.') from None
    def public(self,path):
        old=self.base;self.base='https://api.coinbase.com/api/v3/brokerage'
        try:return self.request('GET',path,public=True)
        finally:self.base=old
    def get_markets(self,category):return self.public('/market/products?product_type=SPOT').get('products',[])
    def get_quote(self,market_id,direction):
        m=self.public('/market/products/'+quote(market_id,safe=''));price=float(m['price'])
        return {'id':market_id,'name':m.get('display_name',market_id),'agent':'crypto','instrument':'spot','venue':self.venue,'price':price,'quoteAt':now(),'closeAt':'2099-01-01T00:00:00+00:00','slippage':0,'open':not any(m.get(k,False) for k in ('is_disabled','trading_disabled','cancel_only','view_only')),'direction':direction,'executionReady':market_id.endswith('-USD') and m.get('product_type','SPOT')=='SPOT' and price>0,'baseIncrement':m['base_increment'],'quoteIncrement':m['quote_increment'],'executionNote':'USD spot opening buys only. Sandbox uses public live quotes with static mock orders.'}
    def get_balance_positions(self):
        rows=[];cursor=None
        for _ in range(100):
            b=self.request('GET','/accounts?limit=250'+('&cursor='+quote(cursor,safe='') if cursor else ''));rows+=b.get('accounts',[])
            if not b.get('has_next'):break
            cursor=b.get('cursor')
            if not cursor:raise VenueError('Incomplete Coinbase balance pagination.')
        else:raise VenueError('Coinbase account pagination exceeded the supported limit.')
        cash=0.;value=0.;positions=[]
        for a in rows:
            currency=a['currency'];available=float(a['available_balance']['value']);held=float(a.get('hold',{}).get('value',0));quantity=available+held
            if currency=='USD':cash+=available;continue
            if quantity:
                price=1.0 if currency=='USDC' else float(self.public('/market/products/'+quote(currency+'-USD',safe=''))['price']);worth=quantity*price;value+=worth;positions.append({'currency':currency,'quantity':quantity,'value':worth})
        daily=0.
        if self.environment=='live':
            start=now()[:10]+'T00:00:00Z';cursor=None;seen=set()
            for _ in range(100):
                path='/orders/historical/fills?limit=100&order_side=BUY&product_types=SPOT&start_sequence_timestamp='+quote(start,safe='')+('&cursor='+quote(cursor,safe='') if cursor else '')
                response=self.request('GET',path)
                if response.get('proof_token_required'):raise VenueError('Coinbase requires additional history authorization for complete daily limits.')
                for f in response.get('fills',[]):
                    if f['entry_id'] in seen:continue
                    seen.add(f['entry_id']);cost=float(f['size']) if f['size_in_quote'] else float(f['size'])*float(f['price'])
                    currency=f['product_id'].split('-')[-1]
                    if currency not in ('USD','USDC'):raise VenueError('Non-USD external fills require currency accounting before trading.')
                    daily+=cost+float(f['commission'])
                cursor=response.get('cursor')
                if not cursor:break
            else:raise VenueError('Incomplete Coinbase daily fills pagination.')
        return {'externalDailySpend':daily,'cash':cash,'positionValue':value,'exposure':value,'realizedPnl':None,'unrealizedPnl':None,'positions':positions,'asOf':now(),'source':'Coinbase static sandbox • keys not verified' if self.environment=='sandbox' else 'Coinbase live account','mock':self.environment=='sandbox'}
    def place_order(self,order):
        m=order['market'];budget=Decimal(str(order['amount']));price=Decimal(str(m['price']));inc=Decimal(m['baseIncrement'])
        if self.environment=='sandbox':
            return self.request('POST','/orders',{'client_order_id':order['id'],'product_id':m['id'],'side':'BUY','order_configuration':{'market_market_ioc':{'quote_size':str(budget)}}})
        summary=self.request('GET','/transaction_summary?product_type=SPOT');rate=Decimal(summary['fee_tier']['taker_fee_rate'])
        if not Decimal('0')<=rate<=Decimal('.1') or summary.get('has_cost_plus_commission') or summary.get('goods_and_services_tax',{}).get('rate') not in (None,'','0','0.0'):
            raise VenueError('Unsupported Coinbase fee or tax schedule; trade not submitted.')
        # Conservative commission reserve: at least 1%, plus $0.02 rounding reserve.
        fee_rate=max(rate,Decimal('.01'));quantity=((budget-Decimal('.02'))/(price*(1+fee_rate))/inc).to_integral_value(rounding=ROUND_DOWN)*inc
        if quantity<=0:raise VenueError('Budget below the product minimum increment.')
        config={'limit_limit_fok':{'base_size':str(quantity),'limit_price':str(price)}}
        preview=self.request('POST','/orders/preview',{'product_id':m['id'],'side':'BUY','order_configuration':config})
        commission=Decimal(str(preview['commission_total']))
        if preview.get('errs') or quantity*price+commission>budget:raise VenueError('Venue preview exceeded the all-in budget or rejected the order.')
        response=self.request('POST','/orders',{'client_order_id':order['id'],'product_id':m['id'],'side':'BUY','order_configuration':config,'preview_id':preview['preview_id']})
        return response
    def get_order(self,order_id):
        return self.request('GET','/orders/historical/'+quote(order_id,safe=''))['order']
    def cancel_orders(self,ids):return self.request('POST','/orders/batch_cancel',{'order_ids':ids})

ADAPTERS={'kalshi':KalshiAdapter,'coinbase':CoinbaseAdapter}
def adapter(venue,credentials,environment):
    if venue not in ADAPTERS:raise VenueError('This venue has no registered adapter yet.')
    return ADAPTERS[venue](credentials,environment)
